from celery import Celery

from beta.core.settings import get_settings

app = Celery("beta")
app.conf.update(broker_url=str(get_settings().broker_url))
