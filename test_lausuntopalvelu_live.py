# test_lausuntopalvelu_live.py
import os
import unittest
from google.cloud import bigquery
from google.auth.exceptions import DefaultCredentialsError

class TestLausuntopalveluLive(unittest.TestCase):
    def test_read_articles_until_lausuntopalvelu_found(self):
        project = os.getenv("GCP_PROJECT", "uutisseuranta-activitystreams")
        dataset = os.getenv("BQ_DATASET", "activitystreams")
        
        try:
            client = bigquery.Client(project=project)
        except DefaultCredentialsError:
            self.skipTest("No GCP credentials found, skipping live integration test.")
            return

        # Kysely hakee uutiset julkaisujärjestyksessä (uusin ensin), kuten outbox tekee
        query = f"""
            SELECT id, source, published
            FROM `{project}.{dataset}.objects`
            WHERE deleted = FALSE
            ORDER BY published DESC NULLS LAST, id ASC
        """
        
        print("\nReading articles from live BigQuery until lausuntopalvelu article is found...")
        query_job = client.query(query)
        
        found = False
        count = 0
        lausuntopalvelu_info = None
        
        for row in query_job.result():
            count += 1
            if row.source == "lausuntopalvelu":
                found = True
                lausuntopalvelu_info = {
                    "id": row.id,
                    "published": row.published,
                    "index": count
                }
                break
                
        if found:
            print(f"✓ Found lausuntopalvelu article at index {count}!")
            print(f"  ID: {lausuntopalvelu_info['id']}")
            print(f"  Published: {lausuntopalvelu_info['published']}")
        else:
            print(f"✗ Read through all {count} articles and did not find any lausuntopalvelu articles.")

        self.assertTrue(found, f"Did not find any lausuntopalvelu article after reading {count} articles.")
        
if __name__ == "__main__":
    unittest.main()
