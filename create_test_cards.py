from server import app, db, User, Card

def create_test_cards(username):
    with app.app_context():
        # Get the user
        user = User.query.filter_by(username=username).first()
        if not user:
            print(f"User {username} not found")
            return
        
        # Sample Swedish vocabulary cards
        test_cards = [
            {
                "front": "Hej",
                "back": "Hello / Hi"
            },
            {
                "front": "Tack",
                "back": "Thank you"
            },
            {
                "front": "Ja",
                "back": "Yes"
            },
            {
                "front": "Nej",
                "back": "No"
            },
            {
                "front": "God morgon",
                "back": "Good morning"
            }
        ]
        
        created = 0
        for card_data in test_cards:
            # Check if card already exists
            existing = Card.query.filter_by(
                user_id=user.id,
                front=card_data["front"]
            ).first()
            
            if not existing:
                card = Card(
                    user_id=user.id,
                    front=card_data["front"],
                    back=card_data["back"]
                )
                db.session.add(card)
                created += 1
        
        if created > 0:
            db.session.commit()
            print(f"Created {created} new cards for user {username}")
        else:
            print("No new cards created (all already exist)")

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python create_test_cards.py <username>")
        sys.exit(1)

    username = sys.argv[1]
    create_test_cards(username)
