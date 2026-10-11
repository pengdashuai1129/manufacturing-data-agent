import sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.conf.app_config import app_config


def test_mysql():
    import pymysql

    cfg = app_config.db_dw

    try:
        conn = pymysql.connect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            database=cfg.database,
            charset="utf8mb4",
        )

        cursor = conn.cursor()

        cursor.execute("SHOW TABLES")

        tables = [row[0] for row in cursor]

        print(
            f"  [OK] MySQL ({cfg.host}:{cfg.port}/{cfg.database})"
            f" — {len(tables)} tables: {tables}"
        )

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"  [FAIL] MySQL: {e}")


def test_qdrant():
    from qdrant_client import QdrantClient

    cfg = app_config.qdrant

    try:
        client = QdrantClient(host=cfg.host, port=cfg.port, check_compatibility=False)
        collections = client.get_collections()
        names = [c.name for c in collections.collections]

        print(
            f"  [OK] Qdrant ({cfg.host}:{cfg.port})"
            f" — {len(names)} collections: {names}"
        )
    except Exception as e:
        print(f"  [FAIL] Qdrant: {e}")


def test_elasticsearch():
    from elasticsearch import Elasticsearch

    cfg = app_config.es

    try:
        es = Elasticsearch(
            f"http://{cfg.host}:{cfg.port}",
            request_timeout=10,
        )
        info = es.info()

        print(
            f"  [OK] ES ({cfg.host}:{cfg.port})"
            f" — version {info['version']['number']},"
            f" cluster {info['cluster_name']}"
        )
    except Exception as e:
        print(f"  [FAIL] ES: {e}")


def test_embedding():
    from langchain_ollama import OllamaEmbeddings

    cfg = app_config.embedding

    try:
        emb = OllamaEmbeddings(
            model=cfg.model,
            base_url=f"http://{cfg.host}:{cfg.port}",
        )
        vector = emb.embed_query("test")

        print(
            f"  [OK] Ollama ({cfg.host}:{cfg.port}/{cfg.model})"
            f" — vector dim {len(vector)}"
        )
    except Exception as e:
        print(f"  [FAIL] Ollama: {e}")


if __name__ == "__main__":
    print("===== Testing infrastructure connections =====\n")
    test_mysql()
    test_qdrant()
    test_elasticsearch()
    test_embedding()
    print("\n===== Done =====")
