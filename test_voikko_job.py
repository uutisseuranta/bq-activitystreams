import unittest
from unittest.mock import MagicMock
import voikko_job


class TestVoikkoJob(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(voikko_job.clean_text("<p>Hello &amp; welcome!</p>"), " Hello & welcome! ")
        self.assertEqual(voikko_job.clean_text(None), "")

    def test_extract_text(self):
        obj = {
            "name": "Pääotsikko",
            "summary": "Lyhyt tiivistelmä",
            "content": "<p>Leipäteksti</p>"
        }
        extracted = voikko_job.extract_text(obj)
        # clean_text korvaa HTML-tagit välilyönneillä, jolloin leipätekstin ympärille jää välilyönnit
        self.assertEqual(extracted, "Pääotsikko Lyhyt tiivistelmä  Leipäteksti ")

        activity = {
            "type": "Create",
            "object": {
                "name": "Note Otsikko",
                "content": "Sisältö"
            }
        }
        self.assertEqual(voikko_job.extract_text(activity), "Note Otsikko Sisältö")

    def test_analyze_tags_uppercase_keys_regression(self):
        # Mockataan libvoikko.Voikko
        mock_voikko = MagicMock()

        # Määritellään analyysivastaukset, jotka käyttävät oikeita suuraakkosavaimia
        # Tokenit tulevat analyysiin alkuperäisessä asussaan (esim. alkukirjaimet suuria)
        def mock_analyze(token):
            token_lower = token.lower()
            if token_lower == "helsingissä":
                return [{"CLASS": "nimisana", "BASEFORM": "Helsinki"}]
            if token_lower == "ja":
                return [{"CLASS": "sidesana", "BASEFORM": "ja"}]
            if token_lower == "euroopan":
                return [{"CLASS": "nimisana", "BASEFORM": "Eurooppa"}]
            return []

        mock_voikko.analyze.side_effect = mock_analyze

        # Syöte sisältää substantiiveja ja konjunktion (ja), jonka pitäisi suodattua pois
        text = "Helsingissä ja Euroopan unionissa"
        tags = voikko_job.analyze_tags(text, mock_voikko)

        # Suodatettujen avainsanojen pitäisi olla perusmuotoisia nimisanoja pienellä alkukirjaimella ilman #-etuliitettä
        # 'ja' pitäisi hylätä, koska sen CLASS on 'sidesana'
        self.assertIn("helsinki", tags)
        self.assertIn("eurooppa", tags)
        self.assertNotIn("ja", tags)


if __name__ == "__main__":
    unittest.main()
