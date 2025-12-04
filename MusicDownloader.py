import argparse
import ctypes
import io
import os
import re
import sys
import requests
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser
from pathlib import Path
from mutagen.mp3 import MP3
from mutagen.id3 import TIT2, TPE1, TPE2, TALB, TDRC, TRCK, APIC, TYER
from ytmusicapi import YTMusic
from PIL import Image

from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, QPushButton, QVBoxLayout, QFormLayout, QHBoxLayout, QTextEdit, QFileDialog, QSpinBox)
from PySide6.QtGui import QPixmap, QFont, QIcon
from PySide6.QtCore import Qt, QThread, Signal

yt = YTMusic()
log_lock = threading.Lock()

class Logger():
    def __init__(self, logger = print):
        self.logger = logger
    
    def out(self, s : str):
        if self.logger:
            self.logger(s)

def start_timer():
    global start_time, end_time
    start_time = time.time()
    end_time = 0

def stop_timer(logger : Logger = Logger()):
    global start_time, end_time, delta_time
    end_time = time.time()
    delta_time = end_time - start_time
    logger.out(f"Stopped after {int(delta_time / 60.0)} minutes and {int(delta_time % 60)} seconds")

def load_config():
    settings = {
        "out_dir": os.path.join(os.getcwd(), "out"),
        "temp_dir": os.path.join(os.getcwd(), "temp"),
        "cover_dir": os.path.join(os.getcwd(), "covers"),
        "starting_index": 0,
        "max_threads": 32
    }

    config_path = Path(os.path.join(os.getcwd(), "MusicDownloader.cfg"))
    if config_path.exists():
        try:
            config = ConfigParser()
            config.read(config_path)
            if 'Settings' in config:
                if 'out_dir' in config['Settings']:
                    settings['out_dir'] = config['Settings']['out_dir'].strip('"').strip("'")
                if 'cover_dir' in config['Settings']:
                    settings['cover_dir'] = config['Settings']['cover_dir'].strip('"').strip("'")
                if 'temp_dir' in config['Settings']:
                    settings['temp_dir'] = config['Settings']['temp_dir'].strip('"').strip("'")
        except Exception as e:
            print(f"Config Error: {e}")
    return settings

def scrape_data(url : str, logger : Logger = Logger(), album_id = None):
    if not album_id:
        if not url: return
        r_is_album_OLAK = re.search(r'list\=(OLAK5uy_.+)', url)
        r_is_album_MPRE = re.search(r'list\=(MPREb_.+)', url)
        r_is_playlist = re.search(r'list\=(PL.+)', url)

        is_playlist = False
        data = None

        if r_is_album_MPRE or r_is_album_OLAK:
            album_id = r_is_album_OLAK.group(1)
            if r_is_album_OLAK:
                album_id = yt.get_album_browse_id(album_id)
            data = yt.get_album(album_id)
        elif r_is_playlist:
            is_playlist = True
            playlist_id = r_is_playlist.group(1)
            data = yt.get_playlist(playlist_id)
        else:
            logger.out("ERROR: CANT PARSE URL")
        if not data: return None
    else: data = yt.get_album(album_id)

    if is_playlist:
        # data_title = data.get("title")
        # data_artist = data.get("author").get("name")
        # data_year = data.get("year")
        # data_type = "playlist"
        # data_cover_url = re.sub(r'=s\d+$', "=s1200", data.get("thumbnails")[0]["url"])
        # data_track_count = data.get("trackCount")
        # data_albumId_cache = list(set(track["album"]["id"] for track in data["tracks"]))
        # data_videoIds = [track["videoId"] for track in data["tracks"]]

        # data["albumId_cache"] = data_albumId_cache
        # data["videoIds"] = data_videoIds

        # data_tracks = []
        # for track in data.get("tracks"):
            
        #     data_tracks.append({
        #         "videoId": track["videoId"],
        #         "title": track["title"],
        #         "artists": [a['name'] for a in track.get("artists", [])],
        #     })
        pass

    else:
        data_title = data.get('title')
        data_artist = ", ".join([a['name'] for a in data.get("artists", [])])
        data_year = str(data.get('year'))
        data_type = data.get('type').lower()
        data_track_count = data.get('trackCount')
        data_cover_url = re.sub(r'w\d+-h\d+', "w1200-h1200", data.get('thumbnails')[0]['url'])
        data_tracks = [{key: ([a['name'] for a in track.get("artists", [])] if key == "artists" else track[key]) for key in ["videoId", "title", "artists", "isAvailable", "videoType", "trackNumber"]} for track in data.get('tracks', [])]

    data = {
        'url': url,
        'title': data_title,
        'artist': data_artist,
        'year': data_year,
        'type': data_type,
        'cover': data_cover_url,
        'trackcount': data_track_count,
        'tracks': data_tracks,
    }

    logger.out(f"Found: {data['title']} - {data['artist']}")
    logger.out(f"Type: {data['type']}")
    logger.out(f"{data["trackcount"]} tracks found:")
    if is_playlist:
        logger.out(f"{"\n".join(f"   {track["title"]}" for track in data['tracks'])}")
    else:
        logger.out(f"{"\n".join(f"   {track["trackNumber"]}. {track["title"]}" for track in data['tracks'])}")

    return data

def get_album_cover(cover_url, logger : Logger = Logger()):
    logger.out("Getting Album Cover...")
    try:
        r = requests.get(cover_url)
        if r.status_code == 200:
            image_data = r.content
            max_size_bytes = 500 * 1024 #500kb
            if len(image_data) <= max_size_bytes: return image_data
            try:
                img = Image.open(io.BytesIO(image_data))
                if img.mode != "RGB": img = img.convert("RGB")
                output = io.BytesIO()
                img.save(output, format="JPEG", quality=85)
                return output.getvalue()
            except:
                return image_data
    except:
        pass
    return None

def save_album_cover(cover, artist, album, dir, logger : Logger = Logger()):
    if dir and cover:
        try:
            safe_art = sanitise(artist); safe_alb = sanitise(album)
            path = dir / f"{safe_art} - {safe_alb}.jpg"
            if not path.exists():
                with open(path, "wb") as f: f.write(cover)
            logger.out("Cover Saved...")
        except: pass

def sanitise(s):
    if not s: return "Unknown"
    return re.sub(r'[<>:"/\\|?*]', '', s).strip()

def download_track(track, data, config, cover_data, logger : Logger = Logger()):
    local_yt_dlp = os.path.join(os.getcwd(), "yt-dlp.exe")
    temp_path = Path(config["temp_dir"])
    final_album_dir = Path(config["out_dir"]) / f"{sanitise(data['artist'])} - {sanitise(data['title'])}"

    artist_string = ", ".join(track['artists'])
    artist_tag_string = "; ".join(track['artists'])
    
    temp_filename = f"{track['videoId']}.mp3"
    temp_file_path = temp_path / temp_filename
    
    final_filename = f"{sanitise(str(track['trackNumber']))}. {sanitise(artist_string)} - {sanitise(track['title'])}.mp3"
    final_file_path = final_album_dir / final_filename

    if final_file_path.exists():
        with log_lock: logger.out(f"Skipping (Exists): {track['title']}")
        return

    cmd = [
        str(local_yt_dlp),
        "-x", "--audio-quality", "0",
        "--no-check-certificates",
        "-f", 'ba[acodec^=mp3]/ba/b',
        "--audio-format", "mp3",
        "--ffmpeg-location", os.getcwd(),
        "-o", os.path.join(temp_path, f"{track['videoId']}.%(ext)s"),
        f"https://www.youtube.com/watch?v={track['videoId']}",
    ]

    startup_info = None
    if os.name == "nt":
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        with log_lock: logger.out(f"Downloading: {track['title']}")
        subprocess.run(cmd,  stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, startupinfo=startup_info)
        with log_lock: logger.out(f"Tagging: {track['title']}")

        try:
            audio = MP3(temp_file_path)
            if audio.tags is None:
                audio.add_tags()            
            audio.tags.delall("APIC")
            audio.tags.add(TIT2(encoding=3, text=track["title"]))
            audio.tags.add(TPE1(encoding=3, text=artist_tag_string))
            audio.tags.add(TPE2(encoding=3, text=artist_string))
            audio.tags.add(TALB(encoding=3, text=data["title"]))
            audio.tags.add(TDRC(encoding=3, text=str(data["year"])))
            audio.tags.add(TYER(encoding=3, text=str(data["year"])))
            audio.tags.add(TRCK(encoding=3, text=str(track["trackNumber"])))
            if cover_data:
                audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='', data=cover_data))
            audio.save(v2_version=3)
        except Exception as e:
            with log_lock: logger.out(f"Tagging Error on {track['title']}: {e}")
        shutil.move(temp_file_path, final_file_path)
        with log_lock: logger.out(f"Finished: {track['title']}")

    except Exception as e:
        with log_lock: logger.out(f"Error processing {track['title']}: {e}")

def download_album(data, config, logger : Logger = Logger(), cover_data=None):
    out_path = Path(config["out_dir"])
    cover_path = Path(config["cover_dir"])
    temp_path = Path(config["temp_dir"])

    final_album_dir = out_path / f"{sanitise(data['artist'])} - {sanitise(data['title'])}"
    
    final_album_dir.mkdir(parents=True, exist_ok=True)
    out_path.mkdir(parents=True, exist_ok=True)
    cover_path.mkdir(parents=True, exist_ok=True)
    temp_path.mkdir(parents=True, exist_ok=True)

    for f in temp_path.glob("*"):
        try: f.unlink()
        except: pass

    logger.out(f"Starting Download: {data['artist']} - {data['title']}")
    if cover_data is None: cover_data = get_album_cover(data["cover"])
    save_album_cover(cover_data, data["artist"], data["title"], cover_path, logger)

    start_timer()

    with ThreadPoolExecutor(max_workers=config["max_threads"]) as executor:
        executor.map(lambda track: download_track(track, data, config, cover_data, logger), data["tracks"])

    stop_timer(logger=logger)

    logger.out(f"{'=' * 10}\nFINISHED DOWNLOADING ALBUM")

class Worker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal()
    data_signal = Signal(dict, bytes)

    def __init__(self, url, config, data_only = False):
        super().__init__()
        self.url = url
        self.config = config
        self.data_only = data_only

    def run(self):
        logger = Logger(logger=self.log_signal.emit)
        data = scrape_data(self.url, logger=logger)
        if data:
            cover_data = get_album_cover(data["cover"], logger=logger)
            self.data_signal.emit(data, cover_data if cover_data else b'')

            if not self.data_only:
                download_album(data, self.config, logger=logger, cover_data=cover_data)

        self.finished_signal.emit()

class MusicDownloaderGUI(QWidget):
    def __init__(self, config):
        super().__init__()
        self.setWindowTitle("Music Downloader")
        self.resize(900,600)

        self.config = config
        self.setup_ui()

    def setup_ui(self):
        main_layout = QHBoxLayout(self)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        form_layout = QFormLayout()

        #url
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste URL here...")
        self.btn_fetch_data = QPushButton("Fetch Data")
        self.btn_fetch_data.setFixedWidth(80)
        self.btn_fetch_data.clicked.connect(lambda: self.fetch_data())
        url_layout = QHBoxLayout()
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.btn_fetch_data)
        form_layout.addRow("URL:", url_layout)

        #out path
        self.out_input = QLineEdit(self.config["out_dir"])
        self.btn_browse_out = QPushButton("...")
        self.btn_browse_out.setFixedWidth(30)
        self.btn_browse_out.clicked.connect(lambda: self.browse_folder(self.out_input))
        out_layout = QHBoxLayout()
        out_layout.addWidget(self.out_input)
        out_layout.addWidget(self.btn_browse_out)
        form_layout.addRow("Output Path:", out_layout)

        #temp path
        self.temp_input = QLineEdit(self.config["temp_dir"])
        self.btn_browse_temp = QPushButton("...")
        self.btn_browse_temp.setFixedWidth(30)
        self.btn_browse_temp.clicked.connect(lambda: self.browse_folder(self.temp_input))
        temp_layout = QHBoxLayout()
        temp_layout.addWidget(self.temp_input)
        temp_layout.addWidget(self.btn_browse_temp)
        form_layout.addRow("Temp Path:", temp_layout)

        #cover path
        self.cover_input = QLineEdit(self.config["cover_dir"])
        self.btn_browse_cover = QPushButton("...")
        self.btn_browse_cover.setFixedWidth(30)
        self.btn_browse_cover.clicked.connect(lambda: self.browse_folder(self.cover_input))
        cover_layout = QHBoxLayout()
        cover_layout.addWidget(self.cover_input)
        cover_layout.addWidget(self.btn_browse_cover)
        form_layout.addRow("Cover Path:", cover_layout)

        #threads
        self.num_threads_input = QSpinBox()
        self.num_threads_input.setRange(1, 128)
        self.num_threads_input.setValue(int(self.config.get("max_threads", 4)))
        self.num_threads_input.setFixedWidth(100)
        form_layout.addRow("Max Threads:", self.num_threads_input)

        left_layout.addLayout(form_layout)

        #start button
        self.start_btn = QPushButton("Start Download")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self.start_process)
        left_layout.addWidget(self.start_btn)

        #console
        self.console_label = QLabel("Console Output:")
        left_layout.addWidget(self.console_label)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet("background-color: #222; color: #EEE; font-family: Consolas, monospace;")
        left_layout.addWidget(self.console)

        right_widget = QWidget()
        right_widget.setFixedWidth(320)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 0, 0, 0)

        #cover image
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(300, 300)
        self.cover_label.setStyleSheet("background-color: #333; border: 1px solid #555;")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setText("No Cover")
        right_layout.addWidget(self.cover_label)

        self.info_console = QTextEdit()
        self.info_console.setReadOnly(True)
        self.info_console.setStyleSheet("background-color: #222; color: #EEE; font-family: Consolas, monospace;")
        self.info_console.setFixedWidth(300)
        right_layout.addWidget(self.info_console, 1)
        
        right_layout.addStretch()

        main_layout.addWidget(left_widget, 1) 
        main_layout.addWidget(right_widget, 0)

    def browse_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "Select Directory", line_edit.text())
        if folder:
            line_edit.setText(folder)

    def log_to_console(self, text):
        self.console.append(text)
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def update_info_panel(self, data, cover_data):
        style_key = "font-weight: bold; color: #FFD700;" 
        style_val = "color: #FFFFFF;"
        
        track_list_html = "<br>".join(
            f"<span style='font-weight: bold; color: #FFFFFF;'>{track['trackNumber']}. </span>"
            f"<span style='color: #FFFFFF;'>{track['title']}</span>"
            for track in data["tracks"]
        )

        indented_tracks = f"<div style='margin-left: 1em;'>{track_list_html}</div>"

        info_text = (
            f"<span style='{style_key}'>Title:</span> <span style='{style_val}'>{data.get('title', 'Unknown')}</span><br>"
            f"<span style='{style_key}'>Artist:</span> <span style='{style_val}'>{data.get('artist', 'Unknown')}</span><br>"
            f"<span style='{style_key}'>Year:</span> <span style='{style_val}'>{data.get('year', 'Unknown')}</span><br>"
            f"<span style='{style_key}'>Type:</span> <span style='{style_val}'>{data.get('type', 'Unknown').capitalize()}</span><br>"
            f"<span style='{style_key}'>Tracks:</span> <span style='{style_val}'>{data.get('trackcount', 0)}</span>"
            f"{indented_tracks}"
        )
        
        self.info_console.setHtml(info_text)
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.minimum())

        if cover_data:
            pixmap = QPixmap()
            pixmap.loadFromData(cover_data)
            self.cover_label.setPixmap(pixmap.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.cover_label.setText("No Cover Found")
    
    def fetch_data(self):
        url = self.url_input.text().strip()
        if not url:
            self.log_to_console("Error: Please enter a URL.")
            return
        
        self.btn_fetch_data.setEnabled(False)
        self.console.clear()
        
        self.worker = Worker(url, config, data_only=True)
        self.worker.log_signal.connect(self.log_to_console)
        self.worker.data_signal.connect(self.update_info_panel)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def start_process(self):
        url = self.url_input.text().strip()
        if not url:
            self.log_to_console("Error: Please enter a URL.")
            return

        config = self.config.copy()
        config["out_dir"] = self.out_input.text()
        config["temp_dir"] = self.temp_input.text()
        config["cover_dir"] = self.cover_input.text()
        config["max_threads"] = self.num_threads_input.value()

        self.btn_fetch_data.setEnabled(False)

        self.start_btn.setEnabled(False)
        self.start_btn.setText("Downloading...")
        self.console.clear()

        self.cover_label.clear()
        self.cover_label.setText("Loading...")

        self.worker = Worker(url, config)
        self.worker.log_signal.connect(self.log_to_console)
        self.worker.data_signal.connect(self.update_info_panel)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def on_finished(self):
        self.btn_fetch_data.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Start Download")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="Album and Playlist Downloader",
        description="Scrapes Album and Playlist data from youtube music, and stores it in catgorized folders"
    )
    parser.add_argument("ytb_url", nargs="?", help="The youtube music URL")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    config = load_config()

    if args.ytb_url:
        logger = Logger(print if args.verbose else None)
        data = scrape_data(args.ytb_url, logger=logger)
        if data:
            if args.verbose: print(f"Downloading: {data["artist"]} - {data["album"]}...")
            download_album(data, config, logger=logger)
            if args.verbose: print(f"Download Finished!")
    else:
        if os.name == 'nt':
            myappid = 'music.downloader.gui.v1'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        app = QApplication(sys.argv)
        if os.path.exists("MusicDownloader.ico"):
            app.setWindowIcon(QIcon("MusicDownloader.ico"))
        window = MusicDownloaderGUI(config)
        window.show()
        sys.exit(app.exec())