from flask import Flask, jsonify, request, render_template, redirect, url_for, session
from functools import wraps
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from livereload import Server
import requests
import os
import click
from dotenv import load_dotenv
load_dotenv()  # Cargar variables de .env


app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

# Database setup
# Cambiar estas claves por valores seguros y aleatorios
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY')
app.config['API_KEY'] = os.environ.get('API_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///users.db')
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    get_remote_address,  # Remove app and change key_func to first argument
    app=app,            # Pass app as a named parameter
    default_limits=["200 per day", "50 per hour"]
)

# User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Card(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    _spanish = db.Column('spanish', db.Text, nullable=False)  # Previously 'front'
    _english = db.Column('english', db.Text, nullable=False)  # Previously 'back'
    spanish_definition = db.Column(db.Text, nullable=True)  # Definition in Spanish
    
    # SM2 Algorithm fields
    easiness_factor = db.Column(db.Float, default=2.5)  # Initial EF is 2.5
    interval = db.Column(db.Integer, default=0)         # Days between reviews
    repetitions = db.Column(db.Integer, default=0)      # Number of times reviewed
    next_review = db.Column(db.DateTime, default=datetime.utcnow)  # Next review date
    
    # Relationships
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', backref=db.backref('cards', lazy=True))
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('spanish', 'english', 'user_id', name='unique_card_per_user'),
    )
    
    @staticmethod
    def normalize_text(text):
        """Normaliza el texto para almacenamiento consistente."""
        if text is None:
            return None
        # Convertir a minúsculas y eliminar espacios extra
        return ' '.join(text.lower().split())
    
    @property
    def spanish(self):
        """Getter para spanish"""
        return self._spanish
    
    @spanish.setter
    def spanish(self, value):
        """Setter para spanish que normaliza el texto"""
        self._spanish = self.normalize_text(value)
    
    @property
    def english(self):
        """Getter para english"""
        return self._english    
    
    @english.setter
    def english(self, value):
        """Setter para english que normaliza el texto"""
        self._english = self.normalize_text(value)
    
    def calculate_next_interval(self, quality):
        """Calculate the next interval using SM2 algorithm.
        
        Args:
            quality (int): Rating from 0 to 5
                0 = complete blackout
                1 = incorrect response; the correct one remembered
                2 = incorrect response; where the correct one seemed easy to recall
                3 = correct response recalled with serious difficulty
                4 = correct response after a hesitation
                5 = perfect response
        """
        # Update easiness factor
        self.easiness_factor = max(
            1.3,  # Minimum EF
            self.easiness_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        )
        
        # Update interval
        if quality < 3:
            # Reset repetitions if answer was wrong
            self.repetitions = 0
            self.interval = 1
        else:
            self.repetitions += 1
            if self.repetitions == 1:
                self.interval = 1
            elif self.repetitions == 2:
                self.interval = 6
            else:
                self.interval = round(self.interval * self.easiness_factor)
        
        # Calculate next review date
        self.next_review = datetime.utcnow() + timedelta(days=self.interval)
        return self.next_review

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def get_due_cards(user_id):
    """Get cards that are due for review for a given user.
    
    Returns cards in this order:
    1. Cards that are due for review (ordered by due date)
    2. New cards (that have never been reviewed)
    """
    now = datetime.utcnow()
    
    # Get cards due for review
    due_cards = Card.query.filter(
        Card.user_id == user_id,
        Card.next_review <= now,
        Card.repetitions > 0  # Not a new card
    ).order_by(Card.next_review).all()
    
    # Get new cards (never reviewed)
    new_cards = Card.query.filter(
        Card.user_id == user_id,
        Card.repetitions == 0
    ).order_by(Card.created_at).all()
    
    return due_cards + new_cards


@app.route('/practice')
@login_required
def practice_page():
    """Render the practice page."""
    next_due = Card.query.filter(
        Card.user_id == current_user.id,
        Card.next_review > datetime.utcnow()
    ).order_by(Card.next_review).first()
    
    next_due_date = next_due.next_review if next_due else None
    
    return render_template('practice.html', next_review=next_due_date)


@app.route('/start-practice')
@login_required
def start_practice():
    """Start a practice session by loading due cards."""
    cards = get_due_cards(current_user.id)
    
    if not cards:
        next_due = Card.query.filter(
            Card.user_id == current_user.id,
            Card.next_review > datetime.utcnow()
        ).order_by(Card.next_review).first()
        
        return jsonify({
            "error": "No cards due for review",
            "next_review": next_due.next_review.isoformat() if next_due else None
        }), 404
    
    # Store card IDs in session
    session['practice_card_ids'] = [card.id for card in cards]
    session['current_card_index'] = 0
    
    # Get first card data
    first_card = cards[0]
    
    return jsonify({
        "success": True,
        "total_cards": len(cards),
        "cardId": first_card.id,
        "question": first_card.english.split('.')[0].strip(),
        "answer": first_card.spanish.split('.')[0].strip()
    })


@app.route('/next-card')
@login_required
def next_card():
    """Get the next card to review."""
    card_ids = session.get('practice_card_ids', [])
    current_index = session.get('current_card_index', 0)
    
    if not card_ids or current_index >= len(card_ids):
        return jsonify({"error": "No more cards"}), 404
    
    card = Card.query.get(card_ids[current_index])
    if not card:
        return jsonify({"error": "Card not found"}), 404
    
    return jsonify({
        "cardId": card.id,
        "question": card.english.split('.')[0].strip(),
        "answer": card.spanish.split('.')[0].strip(),
    })


@app.route('/answer-card', methods=['POST'])
@login_required
def answer_card():
    """Process the answer for a card using SM2 algorithm."""
    data = request.json
    card_id = data.get('cardId')
    ease = data.get('ease')
    
    card = Card.query.get(card_id)
    if not card or card.user_id != current_user.id:
        return jsonify({"error": "Card not found"}), 404
    
    quality_map = {1: 0, 2: 3, 3: 4, 4: 5}
    quality = quality_map.get(ease, 0)
    
    card.calculate_next_interval(quality)
    db.session.commit()
    
    session['current_card_index'] = session.get('current_card_index', 0) + 1
    
    return jsonify({"success": True})


@app.route('/login', methods=['POST'])
@limiter.limit("5 per minute")
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        login_user(user)
        # Return the deck name (username) with the success message
        return jsonify({
            'message': 'Logged in successfully',
            'deck_name': user.username
        })

    return jsonify({'error': 'Invalid username or password'}), 401



@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login_page'))
    return redirect(url_for('practice_page'))

@app.route('/login-page')
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login_page'))

def require_api_key(f):
    """Decorator to require API key for routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if (api_key and api_key == app.config['API_KEY']) or current_user.is_authenticated:
            return f(*args, **kwargs)
        return jsonify({'error': 'Invalid or missing API key'}), 401
    return decorated


@app.route('/api/cards/<username>', methods=['GET'])
@require_api_key
def list_cards(username):
    """List all cards for a specific user (read-only view for Google Sheets).
    
    Args:
        username: The username whose cards to retrieve
    Returns:
        JSON with all cards for the user, sorted by creation date
    """
    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    cards = Card.query.filter_by(user_id=user.id).order_by(Card.created_at.desc()).all()
    
    return jsonify({
        'username': username,
        'total_cards': len(cards),
        'cards': [{
            'spanish': card.spanish,
            'english': card.english,
            'created_at': card.created_at.isoformat(),
            'next_review': card.next_review.isoformat() if card.next_review else None,
            'repetitions': card.repetitions
        } for card in cards]
    })


@app.route('/api/cards', methods=['POST'])
@require_api_key
def sync_cards():
    """Create new cards from Google Sheets without updating existing ones.
    
    Expected JSON format:
    {
        'username': 'user123',
        'cards': [
            {
                'spanish': 'Question',
                'english': 'Answer'
            },
            ...
        ]
    }
    """
    data = request.get_json()
    if not data or 'cards' not in data or 'username' not in data:
        return jsonify({'error': 'Invalid request format'}), 400
    
    user = User.query.filter_by(username=data['username']).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    created = 0
    
    for card_data in data['cards']:
        # Check required fields
        if 'spanish' not in card_data or 'english' not in card_data:
            continue
        
        # Check if a card with the same spanish and english fields already exists
        card = Card.query.filter_by(
            user_id=user.id,
            spanish=card_data['spanish'],
            english=card_data['english']
        ).first()
        
        if not card:
            # Create a new card
            new_card = Card(
                user_id=user.id,
                spanish=card_data['spanish'],
                english=card_data['english']
            )
            db.session.add(new_card)
            created += 1
    
    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'created': created
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/manage-cards', methods=['GET'])
@login_required
def manage_cards():
    """Vista de administración de tarjetas."""
    # Obtener todos los usuarios para el selector
    users = User.query.all()
    
    # Obtener el usuario seleccionado del query parameter
    selected_user_id = request.args.get('user_id', type=int)
    
    # Si no hay usuario seleccionado, usar el usuario actual
    if not selected_user_id:
        selected_user_id = current_user.id
    
    # Obtener las tarjetas del usuario seleccionado
    cards = Card.query.filter_by(user_id=selected_user_id)\
                     .order_by(Card.created_at.desc())\
                     .all()
    
    return render_template('manage_cards.html', 
                         cards=cards, 
                         users=users,
                         selected_user_id=selected_user_id)


@app.route('/api/cards/<int:card_id>', methods=['PUT'])
@login_required
def update_card(card_id):
    try:
        data = request.json
        if not data or not isinstance(data.get('spanish'), str) or not isinstance(data.get('english'), str):
            return jsonify({'error': 'Invalid input data'}), 400
            
        card = Card.query.get_or_404(card_id)
        
        # Verificar que el usuario es dueño de la tarjeta
        if card.user_id != current_user.id:
            return jsonify({'error': 'Unauthorized'}), 403
        
        card.spanish = data['spanish']
        card.english = data['english']
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'card': {
                'id': card.id,
                'spanish': card.spanish,
                'english': card.english
            }
        })
        
    except Exception as e:
        print(f"Error updating card: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
    

##################
#CLI commands ####
##################

@app.cli.command("import-cards")
@click.argument('username')
@click.argument('csv_file')
def import_cards(username, csv_file):
    """Import cards from CSV file for a user."""
    import csv  # Añadir esta línea si no está importado
    
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f"User {username} not found")
            return
        
        created = 0
        skipped = 0
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            # Usar DictReader para manejar correctamente las comas en los campos
            csv_reader = csv.DictReader(f)
            
            # Encontrar las columnas en el CSV
            fieldnames = csv_reader.fieldnames
            spanish_col = next((col for col in fieldnames if 'español' in col.lower()), None)
            english_col = next((col for col in fieldnames if 'ingles' in col.lower() or 'inglés' in col.lower()), None)
            definition_col = next((col for col in fieldnames if 'definición' in col.lower() or 'definicion' in col.lower()), None)
            
            if not spanish_col or not english_col:
                click.echo("Error: No se encontraron las columnas necesarias en el CSV")
                return
            
            # Procesar cada línea
            for row in csv_reader:
                spanish = row[spanish_col].strip()
                english = row[english_col].strip()
                definition = row.get(definition_col, '').strip() if definition_col else None
                
                if not spanish or not english:
                    continue
                
                # Verificar si la tarjeta ya existe
                existing_card = Card.query.filter_by(
                    user_id=user.id,
                    _spanish=Card.normalize_text(spanish),
                    _english=Card.normalize_text(english)
                ).first()
                
                if existing_card:
                    skipped += 1
                    continue
                
                # Crear nueva tarjeta
                card = Card(
                    user_id=user.id,
                    spanish=spanish,
                    english=english,
                    spanish_definition=definition
                )
                db.session.add(card)
                created += 1
                
                # Commit cada 100 tarjetas
                if created % 100 == 0:
                    db.session.commit()
                    click.echo(f"Processed {created} cards...")
        
        # Commit final
        db.session.commit()
        click.echo(f"Successfully imported {created} cards (skipped {skipped} duplicates)")
        
    except Exception as e:
        db.session.rollback()
        click.echo(f"Error: {str(e)}")

@app.cli.command("add-cards")
@click.argument('username')
def add_cards_interactive(username):
    """Añadir tarjetas de forma interactiva para un usuario.
    Pregunta por palabra en español, traducción y opcionalmente definición."""
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f"Usuario {username} no encontrado")
            return
        
        added = 0
        while True:
            # Preguntar por la palabra en español
            spanish = click.prompt('Palabra en español (deja vacío para terminar)', default='', show_default=False)
            if not spanish:
                break
                
            # Preguntar por la traducción
            english = click.prompt('Traducción al inglés', type=str)
            
            # Preguntar por la definición (opcional)
            if click.confirm('¿Quieres añadir una definición?', default=False):
                definition = click.prompt('Definición en español', type=str)
            else:
                definition = None
            
            # Verificar si la tarjeta ya existe
            existing_card = Card.query.filter_by(
                user_id=user.id,
                _spanish=Card.normalize_text(spanish),
                _english=Card.normalize_text(english)
            ).first()
            
            if existing_card:
                click.echo(f"La tarjeta '{spanish} - {english}' ya existe")
                continue
            
            # Crear nueva tarjeta
            card = Card(
                user_id=user.id,
                spanish=spanish,
                english=english,
                spanish_definition=definition
            )
            db.session.add(card)
            added += 1
            
            # Commit después de cada tarjeta
            db.session.commit()
            click.echo(f"Tarjeta añadida: {spanish} - {english}")
        
        click.echo(f"\nSe añadieron {added} tarjetas nuevas")
        
    except Exception as e:
        db.session.rollback()
        click.echo(f"Error: {str(e)}")

@app.cli.command("list-cards")
@click.argument('username')
@click.option('--sort', type=click.Choice(['spanish', 'english', 'date']), default='date', 
              help='Ordenar por: spanish (español), english (inglés) o date (fecha)')
def list_cards_for_user(username, sort):
    """Listar todas las tarjetas de un usuario."""
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f"Usuario {username} no encontrado")
            return
        
        # Preparar la consulta base
        query = Card.query.filter_by(user_id=user.id)
        
        # Aplicar ordenamiento
        if sort == 'spanish':
            query = query.order_by(Card._spanish)
        elif sort == 'english':
            query = query.order_by(Card._english)
        else:  # date
            query = query.order_by(Card.created_at.desc())
        
        cards = query.all()
        
        if not cards:
            click.echo(f"No hay tarjetas para el usuario {username}")
            return
        
        click.echo(f"\nTarjetas de {username} ({len(cards)} total):")
        click.echo("-" * 60)
        
        for i, card in enumerate(cards, 1):
            click.echo(f"{i}. {card.spanish} - {card.english}")
            if card.spanish_definition:
                click.echo(f"   Def: {card.spanish_definition}")
        
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@app.cli.command("check-word")
@click.argument('username')
@click.argument('word')
def check_word(username, word):
    """Comprobar si una palabra en español ya existe para un usuario.
    
    Args:
        username: Nombre del usuario
        word: Palabra en español a buscar
    """
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f"Usuario {username} no encontrado")
            return
        
        # Normalizar la palabra buscada
        normalized_word = Card.normalize_text(word)
        
        # Buscar la palabra
        cards = Card.query.filter(
            Card.user_id == user.id,
            Card._spanish.ilike(f"%{normalized_word}%")  # Búsqueda parcial case-insensitive
        ).all()
        
        if not cards:
            click.echo(f"No se encontró la palabra '{word}' para el usuario {username}")
            return
        
        click.echo(f"\nCoincidencias encontradas para '{word}':")
        click.echo("-" * 60)
        
        for i, card in enumerate(cards, 1):
            click.echo(f"{i}. {card.spanish} - {card.english}")
            if card.spanish_definition:
                click.echo(f"   Def: {card.spanish_definition}")
        
    except Exception as e:
        click.echo(f"Error: {str(e)}")

@app.cli.command("delete-cards")
@click.argument('username')
@click.option('--force', is_flag=True, help='Skip confirmation prompt')
def delete_user_cards(username, force):
    """Borrar todas las tarjetas de un usuario específico."""
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f"Usuario {username} no encontrado")
            return
        
        # Contar tarjetas del usuario
        card_count = Card.query.filter_by(user_id=user.id).count()
        
        if card_count == 0:
            click.echo(f"El usuario {username} no tiene tarjetas")
            return
        
        # Confirmar borrado
        if not force and not click.confirm(
            f'¿Estás seguro de que quieres borrar todas las tarjetas ({card_count}) de {username}?'
        ):
            click.echo("Operación cancelada")
            return
        
        # Borrar tarjetas
        Card.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        
        click.echo(f"Se han borrado {card_count} tarjetas del usuario {username}")
        
    except Exception as e:
        db.session.rollback()
        click.echo(f"Error: {str(e)}")

@app.cli.command("delete-user")
@click.argument('username')
@click.option('--force', is_flag=True, help='Skip confirmation prompt')
def delete_user(username, force):
    """Borrar un usuario y todas sus tarjetas asociadas."""
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f"Usuario {username} no encontrado")
            return
        
        # Contar tarjetas del usuario
        card_count = Card.query.filter_by(user_id=user.id).count()
        
        # Confirmar borrado
        if not force and not click.confirm(
            f'¿Estás seguro de que quieres borrar el usuario {username} y sus {card_count} tarjetas?'
        ):
            click.echo("Operación cancelada")
            return
        
        # Borrar tarjetas y usuario
        Card.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)
        db.session.commit()
        
        click.echo(f"Se ha borrado el usuario {username} y sus {card_count} tarjetas")
        
    except Exception as e:
        db.session.rollback()
        click.echo(f"Error: {str(e)}")

@app.cli.command("list-users")
@click.option('--show-cards', is_flag=True, help='Show card count for each user')
def list_users(show_cards):
    """Listar todos los usuarios registrados."""
    try:
        users = User.query.order_by(User.username).all()
        
        if not users:
            click.echo("No hay usuarios registrados")
            return
            
        click.echo(f"\nUsuarios registrados ({len(users)} total):")
        click.echo("-" * 40)
        
        for i, user in enumerate(users, 1):
            if show_cards:
                card_count = Card.query.filter_by(user_id=user.id).count()
                click.echo(f"{i}. {user.username} ({card_count} tarjetas)")
            else:
                click.echo(f"{i}. {user.username}")
                
    except Exception as e:
        click.echo(f"Error: {str(e)}")

##########################


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    # Configura el servidor con debug=True
    app.debug = True
    
    # Configura livereload
    server = Server(app.wsgi_app)
    server.watch('templates/')
    server.watch('static/')
    server.watch('server.py')  # Añade esto para que observe cambios en server.py
    server.serve(host='0.0.0.0', port=5000)
