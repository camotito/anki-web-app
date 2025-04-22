import click
from flask.cli import with_appcontext
from datetime import datetime
import csv
from server import app, db, User, Card

@app.cli.command("import-cards")
@click.argument('username')
@click.argument('csv_file')
def import_cards(username, csv_file):
    """Import cards from CSV file for a user."""
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f"User {username} not found")
            return
        
        created = 0
        skipped = 0
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            csv_reader = csv.DictReader(f)
            
            fieldnames = csv_reader.fieldnames
            spanish_col = next((col for col in fieldnames if 'español' in col.lower()), None)
            english_col = next((col for col in fieldnames if 'ingles' in col.lower() or 'inglés' in col.lower()), None)
            definition_col = next((col for col in fieldnames if 'definición' in col.lower() or 'definicion' in col.lower()), None)
            
            if not spanish_col or not english_col:
                click.echo("Error: No se encontraron las columnas necesarias en el CSV")
                return
            
            for row in csv_reader:
                spanish = row[spanish_col].strip()
                english = row[english_col].strip()
                definition = row.get(definition_col, '').strip() if definition_col else None
                
                if not spanish or not english:
                    continue
                
                existing_card = Card.query.filter_by(
                    user_id=user.id,
                    _spanish=Card.normalize_text(spanish),
                    _english=Card.normalize_text(english)
                ).first()
                
                if existing_card:
                    skipped += 1
                    continue
                
                card = Card(
                    user_id=user.id,
                    spanish=spanish,
                    english=english,
                    spanish_definition=definition
                )
                db.session.add(card)
                created += 1
                
                if created % 100 == 0:
                    db.session.commit()
                    click.echo(f"Processed {created} cards...")
        
        db.session.commit()
        click.echo(f"Successfully imported {created} cards (skipped {skipped} duplicates)")
        
    except Exception as e:
        db.session.rollback()
        click.echo(f"Error: {str(e)}")

@app.cli.command("add-cards")
@click.argument('username')
def add_cards_interactive(username):
    """Añadir tarjetas de forma interactiva para un usuario."""
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f"Usuario {username} no encontrado")
            return
        
        added = 0
        while True:
            spanish = click.prompt('Palabra en español (deja vacío para terminar)', default='', show_default=False)
            if not spanish:
                break
                
            english = click.prompt('Traducción al inglés', type=str)
            
            if click.confirm('¿Quieres añadir una definición?', default=False):
                definition = click.prompt('Definición en español', type=str)
            else:
                definition = None
            
            existing_card = Card.query.filter_by(
                user_id=user.id,
                _spanish=Card.normalize_text(spanish),
                _english=Card.normalize_text(english)
            ).first()
            
            if existing_card:
                click.echo(f"La tarjeta '{spanish} - {english}' ya existe")
                continue
            
            card = Card(
                user_id=user.id,
                spanish=spanish,
                english=english,
                spanish_definition=definition
            )
            db.session.add(card)
            added += 1
            
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
        
        query = Card.query.filter_by(user_id=user.id)
        
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
    """Comprobar si una palabra en español ya existe para un usuario."""
    try:
        user = User.query.filter_by(username=username).first()
        if not user:
            click.echo(f"Usuario {username} no encontrado")
            return
        
        normalized_word = Card.normalize_text(word)
        
        cards = Card.query.filter(
            Card.user_id == user.id,
            Card._spanish.ilike(f"%{normalized_word}%")
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
        
        card_count = Card.query.filter_by(user_id=user.id).count()
        
        if card_count == 0:
            click.echo(f"El usuario {username} no tiene tarjetas")
            return
        
        if not force and not click.confirm(
            f'¿Estás seguro de que quieres borrar todas las tarjetas ({card_count}) de {username}?'
        ):
            click.echo("Operación cancelada")
            return
        
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
        
        card_count = Card.query.filter_by(user_id=user.id).count()
        
        if not force and not click.confirm(
            f'¿Estás seguro de que quieres borrar el usuario {username} y sus {card_count} tarjetas?'
        ):
            click.echo("Operación cancelada")
            return
        
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

@app.cli.command("create-user")
@click.argument('username')
@click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True)
@click.option('--admin', is_flag=True, help='Create user with admin privileges')
def create_user(username, password, admin):
    """Create a new user."""
    try:
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            click.echo(f"Error: Username '{username}' already exists")
            return

        user = User(username=username, is_admin=admin)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        user_type = "admin" if admin else "regular"
        click.echo(f"Successfully created {user_type} user: {username}")

    except Exception as e:
        db.session.rollback()
        click.echo(f"Error: {str(e)}")