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
        # panggil fungsi login firebase saat pertama kali jalan
        self.db = self._init_firebase()
        # nama tempat simpan data di firestore
        self.collection_name = "visionsafe_knowledge"
        # identitas program biar nggak diblokir website berita
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # kata kunci wajib agar data yang diambil cuma soal mata
        self.eye_keywords = [
            'eye', 'vision', 'blindness', 'myopia', 'glaucoma', 'retina', 'sight', 'screen time', 
            'mata', 'penglihatan', 'rabun', 'miopia', 'katarak', 'gadget', 'kacamata', 'ophthalmology'
        ]

    def _init_firebase(self):
        """fungsi untuk login ke firebase pake config rahasia di github"""
        # ambil data config dari secret github actions
        firebase_config_raw = os.environ.get("FIREBASE_CONFIG")
        if not firebase_config_raw:
            # kalau config nggak ada, program berhenti biar nggak error jauh
            raise ValueError("Environment variable FIREBASE_CONFIG is missing.")
        
        try:
            # ubah teks config jadi format json (dictionary)
            config_dict = json.loads(firebase_config_raw)
            # cek kalau firebase belum nyala, baru nyalain
            if not firebase_admin._apps:
                cred = credentials.Certificate(config_dict)
                firebase_admin.initialize_app(cred)
            # balikkan akses ke firestore database
            return firestore.client()
        except Exception as e:
            # kalau login gagal, kasih tau errornya apa
            print(f"Error initializing Firebase: {e}")
            raise

    def generate_id(self, url):
        """bikin id unik dari link biar nggak ada data double di database"""
        # pake sha256 biar link yang sama selalu dapet id yang sama
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    def is_eye_related(self, text):
        """filter cerdas biar berita yang nggak nyambung nggak masuk"""
        if not text: return False
        # jadiin huruf kecil semua biar pencariannya gampang
        text_lower = text.lower()
        # cek apakah ada salah satu keyword mata di dalam teks
        return any(keyword in text_lower for keyword in self.eye_keywords)

    def extract_full_content(self, url):
        """fungsi buat ambil isi lengkap berita dan buang iklannya"""
        try:
            # buka link beritanya, maksimal tunggu 15 detik
            response = requests.get(url, headers=self.headers, timeout=15)
            # baca struktur html websitenya pake lxml biar cepet
            soup = BeautifulSoup(response.text, 'lxml')
            
            # buang bagian yang nggak penting kayak menu, footer, sama iklan
            for element in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
                element.decompose()

            # ambil semua teks yang ada di dalam tag paragraf <p>
            paragraphs = soup.find_all('p')
            # gabungkan semua paragraf jadi satu teks panjang
            content = " ".join([p.get_text() for p in paragraphs])
            # bersihkan spasi-spasi yang berantakan
            content = " ".join(content.split())
            
            # kalau teksnya terlalu pendek (kurang dari 300 huruf), anggep beritanya nggak valid
            return content if len(content) > 300 else None
        except Exception as e:
            # kalau gagal ambil isi berita, kasih tau errornya
            print(f"Extraction failed for {url}: {e}")
            return None

    def get_data_sources(self):
        """daftar sumber berita resmi yang bakal dipantau tiap hari"""
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
        """mesin utama buat jalanin proses pengambilan data"""
        print(f"--- VisionSafe Data Ingestion Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        # ambil list sumber berita
        sources = self.get_data_sources()
        
        # wadah buat nyatet hasil kerja hari ini
        stats = {
            "total_scanned": 0,
            "total_new": 0,
            "total_skipped": 0,
            "errors": 0
        }
        
        # muter ke tiap sumber berita satu per satu
        for name, config in sources.items():
            print(f"\n[Source] {name}")
            try:
                # kalau tipenya RSS, pake feedparser buat bacanya
                if config["type"] == "rss":
                    feed = feedparser.parse(config["url"])
                    # ambil maksimal 30 berita terbaru aja biar nggak kelamaan
                    entries = feed.entries[:30]
                    
                    for entry in entries:
                        stats["total_scanned"] += 1
                        url = entry.link
                        title = entry.title
                        
                        # kalau sumbernya umum, cek dulu relevansinya soal mata
                        if config["filter"]:
                            combined_text = title + " " + (entry.summary if hasattr(entry, 'summary') else "")
                            if not self.is_eye_related(combined_text):
                                stats["total_skipped"] += 1
                                continue
                        
                        # bikin id unik buat link ini
                        doc_id = self.generate_id(url)
                        # cek ke database, link ini udah ada apa belum
                        doc_ref = self.db.collection(self.collection_name).document(doc_id)
                        
                        # kalau link belum ada di database, proses simpan
                        if not doc_ref.get().exists:
                            # ambil isi lengkap beritanya
                            content = self.extract_full_content(url)
                            if content:
                                # susun data yang mau dikirim ke firebase
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
                                # simpan ke koleksi visionsafe_knowledge
                                doc_ref.set(payload)
                                # catat kalau dapet data baru
                                stats["total_new"] += 1
                                print(f"  [NEW] {title}")
                                # kasih jeda 1 detik biar nggak dianggap serangan ddos
                                time.sleep(1)
                        else:
                            # kalau udah ada, lewati aja
                            stats["total_skipped"] += 1
                            
            except Exception as e:
                # catat kalau ada error pas proses sumber berita tertentu
                print(f"  [ERROR] {name}: {e}")
                stats["errors"] += 1
                continue

        # munculin ringkasan hasil kerja hari ini di log github
        print("\n--- Ingestion Summary ---")
        print(f"Total Scanned : {stats['total_scanned']}")
        print(f"Total New     : {stats['total_new']}")
        print(f"Total Skipped : {stats['total_skipped']}")
        print(f"Errors Found  : {stats['errors']}")
        print(f"Finished at   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# pemicu utama biar program jalan
if __name__ == "__main__":
    ingestor = VisionSafeEliteIngestor()
    ingestor.run()
