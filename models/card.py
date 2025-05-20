from datetime import datetime, timedelta
from . import db

class Card(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    _spanish = db.Column('spanish', db.Text, nullable=False)
    _english = db.Column('english', db.Text, nullable=False)
    spanish_definition = db.Column(db.Text, nullable=True)
    
    # SM2 Algorithm fields
    easiness_factor = db.Column(db.Float, default=2.5)
    interval = db.Column(db.Integer, default=0)
    repetitions = db.Column(db.Integer, default=0)
    next_review = db.Column(db.DateTime, default=datetime.utcnow)
    
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
        """Calculate the next interval using SM2 algorithm."""
        self.easiness_factor = max(
            1.3,
            self.easiness_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        )
        
        if quality < 3:
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
        
        self.next_review = datetime.utcnow() + timedelta(days=self.interval)
        return self.next_review
