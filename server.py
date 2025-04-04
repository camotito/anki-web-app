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


app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

# Database setup
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Change this to a secure secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['API_KEY'] = 'your-secret-api-key-here'  # Change this to a secure API key
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)

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
    spanish = db.Column(db.Text, nullable=False)  # Previously 'front'
    english = db.Column(db.Text, nullable=False)  # Previously 'back'
    
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
    # Get the next due card's date
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
    # Get cards that are due for review
    cards = get_due_cards(current_user.id)
    
    if not cards:
        return jsonify({"error": "No cards due for review"}), 404
    
    # Store the card IDs in the session
    session['practice_card_ids'] = [card.id for card in cards]
    session['current_card_index'] = 0
    
    return jsonify({
        "success": True,
        "total_cards": len(cards)
    })


@app.route('/next-card', methods=['GET'])
@login_required
def next_card():
    """Get the next card to review."""
    # Get card IDs and current index from session
    card_ids = session.get('practice_card_ids', [])
    current_index = session.get('current_card_index', 0)
    
    if not card_ids:
        return jsonify({"error": "No cards due for review"}), 404
    
    if current_index >= len(card_ids):
        return jsonify({"error": "No more cards"}), 404
    
    # Get the current card
    card = Card.query.get(card_ids[current_index])
    if not card:
        return jsonify({"error": "Card not found"}), 404
    
    return jsonify({
        "cardId": card.id,
        "question": card.spanish,
        "answer": card.english
    })


@app.route('/answer-card', methods=['POST'])
@login_required
def answer_card():
    """Process the answer for a card using SM2 algorithm."""
    data = request.json
    card_id = data.get('cardId')
    ease = data.get('ease')  # 1=Again, 2=Hard, 3=Good, 4=Easy
    
    # Get the card
    card = Card.query.get(card_id)
    if not card or card.user_id != current_user.id:
        return jsonify({"error": "Card not found"}), 404
    
    # Map our 1-4 ease to SM2's 0-5 quality
    quality_map = {
        1: 0,  # Again -> Complete blackout
        2: 3,  # Hard -> Correct with difficulty
        3: 4,  # Good -> Correct with hesitation
        4: 5   # Easy -> Perfect response
    }
    quality = quality_map.get(ease, 0)
    
    # Calculate next review date using SM2
    card.calculate_next_interval(quality)
    
    # Save changes
    db.session.commit()
    
    # Move to next card
    session['current_card_index'] = session.get('current_card_index', 0) + 1
    
    return jsonify({"success": True})


@app.route('/login', methods=['POST'])
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
        if api_key and api_key == app.config['API_KEY']:
            return f(*args, **kwargs)
        return jsonify({'error': 'Invalid or missing API key'}), 401
    return decorated


@app.route('/api/cards', methods=['GET'])
@require_api_key
def list_cards():
    """List all cards (for syncing with Google Sheets)."""
    cards = Card.query.all()
    return jsonify({
        'cards': [{
            'id': card.id,
            'spanish': card.spanish,
            'english': card.english,
            'next_review': card.next_review.isoformat() if card.next_review else None,
            'easiness_factor': card.easiness_factor,
            'interval': card.interval,
            'repetitions': card.repetitions,
            'user_id': card.user_id
        } for card in cards]
    })


@app.route('/api/cards', methods=['POST'])
@require_api_key
def sync_cards():
    """Create or update cards from Google Sheets.
    
    Expected JSON format:
    {
        'user_id': 1,
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
    if not data or 'cards' not in data or 'user_id' not in data:
        return jsonify({'error': 'Invalid request format'}), 400
    
    user = User.query.get(data['user_id'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    created = 0
    updated = 0
    
    for card_data in data['cards']:
        # Check required fields
        if 'spanish' not in card_data or 'english' not in card_data:
            continue
        
        # Try to find existing card with same front
        card = Card.query.filter_by(
            user_id=user.id,
            spanish=card_data['spanish']
        ).first()
        
        if card:
            # Update existing card
            card.english = card_data['english']
            updated += 1
        else:
            # Create new card
            card = Card(
                user_id=user.id,
                spanish=card_data['spanish'],
                english=card_data['english']
            )
            db.session.add(card)
            created += 1
    
    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'created': created,
            'updated': updated
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    server = Server(app.wsgi_app)
    server.watch('templates/')
    server.watch('static/')
    server.serve(host='0.0.0.0', port=5000, debug=True)
