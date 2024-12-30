import logging

from datetime import datetime

from .models import AdsBlockLog


class SQLiteHandler(logging.Handler):
    def __init__(self, config, sqlite):
        super().__init__()

        self.session = sqlite.session
        self.sqlite = sqlite

    def emit(self, message):
        dt = datetime.utcfromtimestamp(message.created)

        row = AdsBlockLog(
            module=message.module,
            key=message.levelname.lower(),
            value=message.getMessage(),
            created_on=dt,
            updated_on=dt,
        )

        self.session.add(row)
        self.session.commit()
