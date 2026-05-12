import os
import json
import hashlib
import feedparser
import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import time

class VisionSafeIngestor:
    def __init__(self):
        self.db = self._init_firebase()
        self.collection_name = "visionsafe_knowledge"
        self.keywords = [
            "kesehatan mata", "miopia", "katarak", 
            "bahaya gadget", "glaukoma", "kesehatan retina"
        ]
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

    def _init_firebase(self):
        """Initializes Firebase from environment variable."""
        firebase_config_raw = os.environ.get("FIREBASE_CONFIG")
        if not firebase_config_raw:
            raise ValueError("Environment variable FIREBASE_CONFIG is missing.")
        
        try:
            config_dict = json.loads(firebase_config_raw)
            cred = credentials.Certificate(config_dict)
            firebase_admin.initialize_app(cred)
            return firestore.client()
        except Exception as e:
            print(f"Error initializing Firebase: {e}")
            raise

    def generate_id(self, url):
        """Generates a SHA-256 hash for deduplication."""
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    def clean_html(self, html_content):
        """Removes HTML tags and cleans whitespace using lxml for performance."""
        if not html_content:
            return ""
        try:
            soup = BeautifulSoup(html_content, "lxml")
            return soup.get_text(separator=' ', strip=True)
        except Exception:
            # Fallback to html.parser if lxml fails
            soup = BeautifulSoup(html_content, "html.parser")
            return soup.get_text(separator=' ', strip=True)

    def fetch_news(self, limit_per_run=50):
        """Fetches news from Google News RSS for defined keywords."""
        articles = []
        seen_urls = set()

        for keyword in self.keywords:
            print(f"Fetching news for keyword: {keyword}")
            rss_url = f"https://news.google.com/rss/search?q={keyword}&hl=id&gl=ID&ceid=ID:id"
            
            try:
                feed = feedparser.parse(rss_url)
                for entry in feed.entries:
                    if len(articles) >= limit_per_run:
                        break
                    
                    url = entry.link
                    if url in seen_urls:
                        continue
                    
                    # Basic extraction
                    article_data = {
                        "title": entry.title,
                        "url": url,
                        "published_at": entry.published,
                        "source": entry.source.title if hasattr(entry, 'source') else "Google News",
                        "summary_raw": entry.summary if hasattr(entry, 'summary') else "",
                        "keyword": keyword,
                        "ingested_at": firestore.SERVER_TIMESTAMP
                    }
                    
                    # Clean summary
                    article_data["summary"] = self.clean_html(article_data["summary_raw"])
                    
                    articles.append(article_data)
                    seen_urls.add(url)
                    
            except Exception as e:
                print(f"Error fetching RSS for {keyword}: {e}")
                continue
                
            if len(articles) >= limit_per_run:
                break

        return articles

    def run(self):
        """Main execution flow."""
        print(f"Starting ingestion process at {datetime.now()}")
        
        try:
            articles = self.fetch_news(limit_per_run=50)
            print(f"Found {len(articles)} articles.")
            
            success_count = 0
            for article in articles:
                doc_id = self.generate_id(article["url"])
                doc_ref = self.db.collection(self.collection_name).document(doc_id)
                
                # Check if exists (deduplication)
                if not doc_ref.get().exists:
                    doc_ref.set(article)
                    success_count += 1
                
            print(f"Ingestion complete. Successfully saved {success_count} new articles.")
            
        except Exception as e:
            print(f"Ingestion failed: {e}")

if __name__ == "__main__":
    ingestor = VisionSafeIngestor()
    ingestor.run()
