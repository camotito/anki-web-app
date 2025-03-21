from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

# Variables globales para almacenar las tarjetas del mazo
current_card_index = 0
cards = []

def get_cards_from_deck(deck_name):
    global cards
    # Paso 1: Obtener IDs de las notas en el mazo
    payload_find = {
        "action": "findNotes",
        "version": 6,
        "params": {"query": f"deck:{deck_name}"}
    }
    response_find = requests.post("http://localhost:8765", json=payload_find).json()
    
    if not response_find.get("result"):
        return []
    
    # Paso 2: Obtener contenido de las notas
    payload_info = {
        "action": "notesInfo",
        "version": 6,
        "params": {"notes": response_find["result"]}
    }
    response_info = requests.post("http://localhost:8765", json=payload_info).json()
    
    # Extraer campos de cada nota (asumiendo que el primer campo es la pregunta y el segundo la respuesta)
    return [
        {
            "id": note["noteId"],
            "question": note["fields"].get("Front", {}).get("value", "Sin pregunta"),
            "answer": note["fields"].get("Back", {}).get("value", "Sin respuesta")
        } for note in response_info["result"]
    ]

@app.route('/start-practice/<deck_name>', methods=['GET'])
def start_practice(deck_name):
    global current_card_index, cards
    cards = get_cards_from_deck(deck_name)
    current_card_index = 0
    if not cards:
        return jsonify({"error": f"No hay tarjetas en el mazo '{deck_name}'"}), 404
    return jsonify({"success": True, "total_cards": len(cards)})

@app.route('/next-card', methods=['GET'])
def next_card():
    global current_card_index
    if current_card_index >= len(cards):
        return jsonify({"error": "No hay más tarjetas"}), 404
    
    card = cards[current_card_index]
    return jsonify({
        "question": card["question"],
        "answer": card["answer"]
    })

@app.route('/answer-card', methods=['POST'])
def answer_card():
    global current_card_index
    ease = request.json.get("ease")  # 1=Again, 2=Hard, 3=Good, 4=Easy
    card_id = cards[current_card_index]["id"]
    
    # Enviar la respuesta a Anki
    payload = {
        "action": "guiAnswerCard",
        "version": 6,
        "params": {
            "card": card_id,
            "ease": ease
        }
    }
    response = requests.post("http://localhost:8765", json=payload)
    
    # Avanzar a la siguiente tarjeta
    current_card_index += 1
    return jsonify({"success": True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

