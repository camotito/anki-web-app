from flask import Flask, jsonify, request, render_template, redirect, url_for
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import os

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

# Database setup
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Change this to a secure secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)

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

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

ANKI_CONNECT_URL = "http://localhost:8765"

# Variables globales para almacenar las tarjetas y el índice actual
cards = []
current_card_index = 0


def get_sorted_cards(deck_name):
    """Obtiene las tarjetas del mazo ordenadas por tipo (learning, new, review)."""
    queries = {
        "learning": f"deck:{deck_name} is:learn",
        "new": f"deck:{deck_name} is:new",
        "review": f"deck:{deck_name} is:review"
    }
    
    card_ids = []
    for card_type in ["learning", "new", "review"]:
        payload = {
            "action": "findCards",
            "version": 6,
            "params": {"query": queries[card_type]}
        }
        response = requests.post(ANKI_CONNECT_URL, json=payload).json()
        card_ids.extend(response["result"])
    
    # Obtener información detallada de las tarjetas
    if not card_ids:
        return []
    
    payload_info = {
        "action": "cardsInfo",
        "version": 6,
        "params": {"cards": card_ids}
    }
    cards_info = requests.post(ANKI_CONNECT_URL, json=payload_info).json()["result"]
    
    # Ordenar por tipo de tarjeta (learning -> new -> review)
    return sorted(cards_info, key=lambda x: (
        0 if x["queue"] == 1 else  # Learning
        1 if x["queue"] == 0 else  # New
        2                           # Review
    ))


@app.route('/practice')
@login_required
def practice_page():
    """Render the practice page"""
    return render_template('practice.html')

@app.route('/start-practice', methods=['GET'])
@login_required
def start_practice():
    """Inicia la práctica cargando las tarjetas del mazo del usuario."""
    # Use current user's username as deck name
    deck_name = current_user.username
        
    global cards, current_card_index
    cards = get_sorted_cards(deck_name)
    current_card_index = 0
    
    if not cards:
        return jsonify({"error": f"No hay tarjetas pendientes en el mazo '{deck_name}'"}), 404
    
    return jsonify({"success": True, "total_cards": len(cards)})


@app.route('/next-card', methods=['GET'])
@login_required
def next_card():
    """Devuelve la siguiente tarjeta para practicar."""
    # Security check: ensure there are cards loaded
    if not cards:
        return jsonify({"error": "No cards loaded"}), 400
    global current_card_index
    if current_card_index >= len(cards):
        return jsonify({"error": "No hay más tarjetas"}), 404
    
    card = cards[current_card_index]
    return jsonify({
        "cardId": card["cardId"],
        "question": card["fields"]["Front"]["value"],
        "answer": card["fields"]["Back"]["value"]
    })


@app.route('/answer-card', methods=['POST'])
@login_required
def answer_card():
    # Security check: ensure there are cards loaded
    if not cards:
        return jsonify({"error": "No cards loaded"}), 400
    """Envía la respuesta de la tarjeta a Anki y avanza a la siguiente tarjeta."""
    global current_card_index
    data = request.json
    card_id = data["cardId"]
    ease = data["ease"]  # 1=Again, 2=Hard, 3=Good, 4=Easy
    
    # Enviar respuesta a Anki
    payload = {
        "action": "answerCards",
        "version": 6,
        "params": {
            "answers": [{
                "cardId": card_id,
                "ease": ease
            }]
        }
    }
    response = requests.post(ANKI_CONNECT_URL, json=payload).json()
    
    if response.get("error"):
        return jsonify({"error": response["error"]}), 500
    
    # Avanzar a la siguiente tarjeta
    current_card_index += 1
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000)
		