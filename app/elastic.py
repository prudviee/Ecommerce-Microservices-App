from elasticsearch import Elasticsearch
from app.config import settings

es_client = Elasticsearch(settings.ES_HOST)


def get_es():
    return es_client
