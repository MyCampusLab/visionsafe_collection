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

class VisionSafeEliteIngestor:
    def __init__(self):
        # 1. panggil fungsi login firebase pas pertama kali script jalan
        self.db = self._init_firebase()
        # 2. nama tabel/koleksi di database firestore buat nyimpen semua data
        self.collection_name = "visionsafe_knowledge"
        # 3. nyamar jadi browser beneran biar pas narik data gak diblokir sama website beritanya
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # 4. kamus keyword filter. kalo berita gak ada unsur kata-kata ini, langsung kita buang
        self.eye_keywords = [
            'eye', 'vision', 'blindness', 'myopia', 'glaucoma', 'retina', 'sight', 'screen time', 
            'mata', 'penglihatan', 'rabun', 'miopia', 'katarak', 'gadget', 'kacamata', 'ophthalmology'
        ]

    def _init_firebase(self):
        """fungsi buat ngonek ke server firebase. password dan config ditaruh di github secrets biar aman"""
        firebase_config_raw = os.environ.get("FIREBASE_CONFIG")
        if not firebase_config_raw:
            raise ValueError("waduh, environment variable FIREBASE_CONFIG ilang nih.")
        
        try:
            # ubah teks config jadi format json (dictionary)
            config_dict = json.loads(firebase_config_raw)
            # ngecek kalo firebase belum jalan, baru kita nyalain
            if not firebase_admin._apps:
                cred = credentials.Certificate(config_dict)
                firebase_admin.initialize_app(cred)
            return firestore.client()
        except Exception as e:
            print(f"gagal login ke firebase bro: {e}")
            raise

    def generate_id(self, url):
        """fungsi hashing sha-256: bikin id unik dari link web biar gak ada berita yang masuk dobel"""
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    def is_eye_related(self, text):
        """fungsi filter pinter. kalo teksnya murni ngomongin mata, balikin true."""
        if not text: return False
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.eye_keywords)

    def extract_full_content(self, url):
        """fungsi brutal buat ngambil isi tulisan penuh dari web dan ngebuang iklannya"""
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            soup = BeautifulSoup(response.text, 'lxml')
            
            # basmi elemen sampah kayak menu, footer, iframe, dan script js
            for element in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
                element.decompose()

            # comot teks dari tag <p> aja
            paragraphs = soup.find_all('p')
            content = " ".join([p.get_text() for p in paragraphs])
            # bersihin spasi berlebih
            content = " ".join(content.split())
            
            # kalo teksnya kedikitan (kurang dari 300 huruf), anggep itu bukan artikel beneran
            return content if len(content) > 300 else None
        except Exception as e:
            print(f"gagal narik konten dari {url}: {e}")
            return None

    def get_data_sources(self):
        """daftar target sumber berita. bisa ditambahin terus kedepannya"""
        return {
            "NIH National Eye Institute": {"url": "https://www.nei.nih.gov/about/news-and-events/news/feed", "type": "rss", "filter": False},
            "WHO Global News": {"url": "https://www.who.int/rss-feeds/news-english.xml", "type": "rss", "filter": True},
            "Medical News Today (Eye)": {"url": "https://www.medicalnewstoday.com/rss/eye-health", "type": "rss", "filter": False},
            "Science Daily (Eye Care)": {"url": "https://www.sciencedaily.com/rss/health_medicine/eye_care.xml", "type": "rss", "filter": False},
            "News Medical (Eye Health)": {"url": "https://www.news-medical.net/tag/feed/Eye-Health.aspx", "type": "rss", "filter": False},
            "Kemenkes RI (Sehat Negeriku)": {"url": "https://sehatnegeriku.kemkes.go.id/feed/", "type": "rss", "filter": True},
            "Google News (Eye Health ID)": {"url": "https://news.google.com/rss/search?q=kesehatan+mata+miopia+katarak&hl=id&gl=ID&ceid=ID:id", "type": "rss", "filter": False}
        }

    def run(self):
        """mesin utama yang bakal dijalanin sama github actions"""
        print(f"--- mulai gas narik data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        sources = self.get_data_sources()
        
        # log catatan kerja
        stats = {
            "total_scanned": 0,
            "total_new": 0,
            "total_skipped": 0,
            "errors": 0
        }
        
        for name, config in sources.items():
            print(f"\n[target: {name}]")
            try:
                if config["type"] == "rss":
                    # parse rss feed-nya
                    feed = feedparser.parse(config["url"])
                    
                    # sikat semua berita dari feed tanpa batasan waktu dan jumlah
                    # murni konsep big data: tarik semua, nanti urusan duplikat di-handle firebase
                    entries = feed.entries
                    
                    for entry in entries:
                        stats["total_scanned"] += 1
                        url = entry.link
                        title = entry.title
                        
                        # filter topik buat sumber web yang campur-campur (misal: who atau kemenkes)
                        if config["filter"]:
                            combined_text = title + " " + (entry.summary if hasattr(entry, 'summary') else "")
                            if not self.is_eye_related(combined_text):
                                stats["total_skipped"] += 1
                                continue
                        
                        # ngecek ke database firestore pake hash id. kalo udah ada, skip aja.
                        doc_id = self.generate_id(url)
                        doc_ref = self.db.collection(self.collection_name).document(doc_id)
                        
                        if not doc_ref.get().exists:
                            # kalo belum ada di db, gass eksekusi ambil full teksnya
                            content = self.extract_full_content(url)
                            if content:
                                # rakit json datanya buat dikirim ke cloud
                                payload = {
                                    "title": title,
                                    "url": url,
                                    "full_content": content,
                                    "source": name,
                                    "category": "Authoritative Intelligence",
                                    "published_raw": entry.get("published", datetime.now().strftime("%Y-%m-%d")),
                                    "collected_at": firestore.SERVER_TIMESTAMP,
                                    "content_length": len(content),
                                    "fingerprint": doc_id
                                }
                                # push ke firestore
                                doc_ref.set(payload)
                                stats["total_new"] += 1
                                print(f"  [data baru dapet nih] {title}")
                                # jeda sopan santun 1 detik biar ip github gak diblokir karena dikira ddos
                                time.sleep(1)
                        else:
                            # kalo udah ada di database (hasil scraping 15 menit lalu), lewatin
                            stats["total_skipped"] += 1
                            
            except Exception as e:
                print(f"  [ada yang error nih bro dari {name}]: {e}")
                stats["errors"] += 1
                continue

        # rekap hasil nambang hari ini
        print("\n--- rekap data hari ini ---")
        print(f"total dicek  : {stats['total_scanned']}")
        print(f"data baru    : {stats['total_new']}")
        print(f"data dilewat : {stats['total_skipped']}")
        print(f"jumlah error : {stats['errors']}")
        print(f"kelar jam    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    ingestor = VisionSafeEliteIngestor()
    ingestor.run()
