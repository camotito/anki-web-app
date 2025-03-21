from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

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


@app.route('/start-practice/<deck_name>', methods=['GET'])
def start_practice(deck_name):
    """Inicia la práctica cargando las tarjetas del mazo."""
    global cards, current_card_index
    cards = get_sorted_cards(deck_name)
    current_card_index = 0
    
    if not cards:
        return jsonify({"error": f"No hay tarjetas pendientes en el mazo '{deck_name}'"}), 404
    
    return jsonify({"success": True, "total_cards": len(cards)})


@app.route('/next-card', methods=['GET'])
def next_card():
    """Devuelve la siguiente tarjeta para practicar."""
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
def answer_card():
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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
		