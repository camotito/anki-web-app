from app import app, db, User

def create_user(username, password):
    with app.app_context():
        # Check if user already exists
        if User.query.filter_by(username=username).first():
            print(f"User {username} already exists")
            return
        
        # Create new user
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"User {username} created successfully")

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python create_users.py <username> <password>")
        sys.exit(1)

    username = sys.argv[1]
    password = sys.argv[2]

    create_user(username, password)
