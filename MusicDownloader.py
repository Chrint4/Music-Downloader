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
# import json
from copy import deepcopy
import winsound
from concurrent.futures import ThreadPoolExecutor, as_completed
from configparser import ConfigParser
from pathlib import Path
from mutagen.mp3 import MP3
from mutagen.id3 import TIT2, TPE1, TPE2, TALB, TDRC, TRCK, APIC, TYER
from ytmusicapi import YTMusic
from PIL import Image

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, 
    QVBoxLayout, QFormLayout, QHBoxLayout, QTextEdit, 
    QFileDialog, QSpinBox, QCheckBox, QListWidget, QListWidgetItem, QSizePolicy
)
from PySide6.QtGui import QPixmap, QFont, QIcon
from PySide6.QtCore import Qt, QThread, Signal

yt = YTMusic()
log_lock = threading.Lock()

file_lock = threading.Lock()
playlist_write_index = 1

class Logger():
    def __init__(self, logger = print):
        self.logger = logger
    
    def out(self, s : str, also_print = False):
        if self.logger:
            with log_lock:
                self.logger(s)
                if also_print and self.logger is not print:
                    print(s)

    def err(self, s : str, also_print = False):
        pass

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
        "max_threads": 4,
        "download_lyrics": True,
        "artist_album_only": True,
    }

    config_path = Path(os.path.join(os.getcwd(), "MusicDownloader.cfg"))
    if config_path.exists():
        try:
            config = ConfigParser()
            config.read(config_path)
            if 'Settings' in config:
                def clean_path(path_str):
                    cleaned = path_str.strip('"').strip("'")
                    return os.path.expanduser(os.path.expandvars(cleaned))

                if 'out_dir' in config['Settings']:
                    settings['out_dir'] = clean_path(config['Settings']['out_dir'])
                if 'cover_dir' in config['Settings']:
                    settings['cover_dir'] = clean_path(config['Settings']['cover_dir'])
                if 'temp_dir' in config['Settings']:
                    settings['temp_dir'] = clean_path(config['Settings']['temp_dir'])
                if "max_threads" in config["Settings"]:
                    settings['max_threads'] = int(config['Settings']['max_threads'])
                if "download_lyrics" in config["Settings"]:
                    settings['download_lyrics'] = bool(config['Settings']['download_lyrics'])
        except Exception as e:
            print(f"Config Error: {e}")
    return settings

def scrape_data(url : str = "", logger : Logger = Logger(), album_id = None, config = {}):
    is_playlist = False
    r_is_artist = False
    data = None

    if not album_id:
        if not url: return
        r_is_album_OLAK = re.search(r'list\=(OLAK5uy_.+)', url)
        r_is_album_MPRE = re.search(r'list\=(MPREb_.+)', url)
        r_is_playlist = re.search(r'list\=(PL.+)', url)
        r_is_artist = re.search(r'channel/(UC.+)', url)

        if r_is_album_MPRE or r_is_album_OLAK:
            album_id = r_is_album_OLAK.group(1)
            if r_is_album_OLAK:
                album_id = yt.get_album_browse_id(album_id)
            data = yt.get_album(album_id)
        elif r_is_playlist:
            is_playlist = True
            playlist_id = r_is_playlist.group(1)
            data = yt.get_playlist(playlist_id)
        elif r_is_artist:
            artist_id = r_is_artist.group(1)
            logger.out(f"Fetching Artist: {artist_id}")
            data = yt.get_artist(artist_id)
        else:
            logger.out("ERROR: CANT PARSE URL")
        if not data: return None
    else: data = yt.get_album(album_id)

    if is_playlist:
        data_title = data.get("title")
        data_artist = data.get("author").get("name")
        data_year = data.get("year")
        data_type = "playlist"
        data_cover_url = re.sub(r'=s\d+$', "=s1200", data.get("thumbnails")[0]["url"])
        data_track_count = data.get("trackCount")
        data_albumId_cache = list(set(track["album"]["id"] for track in data["tracks"]))
        data_videoIds = [track["videoId"] for track in data["tracks"]]

        data_tracks = []
        for track in data.get("tracks"):
            data_tracks.append({
                "videoId": track["videoId"],
                "title": track["title"],
                "artists": [a['name'] for a in track.get("artists", [])],
            })

    elif r_is_artist:
        data_artist = data["name"]
        
        def fetch_full_list(section_key):
            section = data.get(section_key)
            if not section: return []
            if 'browseId' in section:
                logger.out(f"Fetching full list for: {section_key}...")
                try:
                    return yt.get_artist_albums(section['browseId'], section.get('params'))
                except Exception as e:
                    logger.out(f"Error fetching {section_key}: {e}")
                    return section.get('results', [])
            return section.get('results', [])

        full_albums = fetch_full_list("albums")
        full_singles = []
        if not config.get("artist_album_only", False):
            full_singles = fetch_full_list("singles")
            full_eps = fetch_full_list("ep")
            full_singles.extend(full_eps)

        logger.out(f"Processing {len(full_albums)} albums and {len(full_singles)} singles/EPs...")
        
        all_items = full_albums + full_singles
        data_albums = []
        for item in all_items:
            data_albums.append({
                "browseId": item["browseId"],
                "title": item.get("title", "Unknown Title"),
                "type": item.get("year", "Album")
            })

        data_cover_url = ""
        if 'thumbnails' in data and data['thumbnails']:
             data_cover_url = re.sub(r'w\d+-h\d+', "w1200-h1200", data['thumbnails'][0]['url'])

        data = {
            'url': url,
            'artist': data_artist,
            'cover': data_cover_url,
        }
        data["albums"] = data_albums
        data["albumCount"] = len(data_albums)
        data["type"] = "artist"

        logger.out(f"Found: {data['artist']}")
        logger.out(f"{data['albumCount']} releases found.")
        logger.out(f"\n".join(f"   {i}. {alb['title']}" for i, alb in enumerate(data['albums'][:10])))
        
        return data
    else:
        data_title = data.get('title')
        data_artist = ", ".join([a['name'] for a in data.get("artists", [])])
        data_year = str(data.get('year'))
        data_type = data.get('type', 'album').lower()
        data_cover_url = re.sub(r'w\d+-h\d+', "w1200-h1200", data.get('thumbnails')[0]['url'])
        data_track_count = data.get('trackCount')
        data_tracks = [{key: ([a['name'] for a in track.get("artists", [])] if key == "artists" else track[key]) for key in ["videoId", "title", "artists", "trackNumber", "duration_seconds"]} for track in data.get('tracks', [])]

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

    if is_playlist:
        data["albumId_cache"] = data_albumId_cache
        data["videoIds"] = data_videoIds

    logger.out(f"Found: {data['title']} - {data['artist']}")
    logger.out(f"Type: {data['type']}")
    logger.out(f"{data['trackcount']} tracks found:")

    return data

def scrape_album(album_id : str, logger : Logger = Logger(), config = {}) -> dict[str, any]:
    ytmusicapi_data = yt.get_album(album_id)

    cmd = [
        str(os.path.join(os.getcwd(), "yt-dlp.exe")),
        "--flat-playlist",
        "--print",
        "%(id)s",
        ytmusicapi_data["audioPlaylistId"],
    ]

    ytdlp_data = subprocess.run(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True,
                                creationflags=0x08000000,
                                )
    
    ytdlp_data = ytdlp_data.stdout.strip().splitlines()

    data_title = ytmusicapi_data.get('title')
    data_artist = ", ".join([a['name'] for a in ytmusicapi_data.get("artists", [])])
    data_year = str(ytmusicapi_data.get('year'))
    data_type = ytmusicapi_data.get('type', 'album').lower()
    data_cover_url = re.sub(r'w\d+-h\d+', "w1200-h1200", ytmusicapi_data.get('thumbnails')[0]['url'])
    data_track_count = ytmusicapi_data.get('trackCount')

    data_tracks = [
        {
            "videoId": video_id,
            "title": track.get("title"),
            "artists": [a["name"] for a in track.get("artists", [])],
            "trackNumber": track.get("trackNumber"),
            "duration_seconds": track.get("duration_seconds"),
        }
        for track, video_id in zip(ytmusicapi_data.get('tracks', []), ytdlp_data)
    ]

    data = {
        'url': album_id,
        'title': data_title,
        'artist': data_artist,
        'year': data_year,
        'type': data_type,
        'cover': data_cover_url,
        'trackcount': data_track_count,
        'tracks': data_tracks,
    }

    return data

def scrape_playlist(url, playlist_id):
    ytmusicapi_data = yt.get_playlist(playlist_id)

    cmd = [
            str(os.path.join(os.getcwd(), "yt-dlp.exe")),
            "--flat-playlist",
            "--print",
            "%(id)s",
            url,
        ]

    ytdlp_data = subprocess.run(cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                    creationflags=0x08000000,
                                    )
        
    ytdlp_data = ytdlp_data.stdout.strip().splitlines()

    data_title = ytmusicapi_data.get("title")
    data_artist = ytmusicapi_data.get("author").get("name")
    data_year = ytmusicapi_data.get("year")
    data_type = "playlist"
    data_cover_url = re.sub(r'=s\d+$', "=s1200", ytmusicapi_data.get("thumbnails")[0]["url"])
    data_track_count = ytmusicapi_data.get("trackCount")

    data_tracks = []
    for track, videoId in zip(ytmusicapi_data.get("tracks"), ytdlp_data):
        data_tracks.append({
                "videoId": videoId,
                "title": track["title"],
                "artists": [a['name'] for a in track.get("artists", [])],
                "album_name": track["album"]["name"],
                "album_id": track["album"]["id"],
            })

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
    
    return data

def new_scrape_data(url : str = None, logger : Logger = Logger(), config = {}, album_id = None):
    if url is None:
        return
    
    is_album = re.search(r'list\=(OLAK5uy_.+)', url)
    is_playlist = re.search(r'list\=(PL.+)', url)
    is_artist = re.search(r'channel/(UC.+)', url)

    if is_album:
        album_id = is_album.group(1)
        album_id = yt.get_album_browse_id(album_id)
        data = scrape_album(album_id, logger)

        logger.out(f"Found: {data['title']} - {data['artist']}")
        logger.out(f"Type: {data['type']}")
        logger.out(f"{len(data["tracks"])} tracks found:")

    elif is_playlist:
        playlist_id = is_playlist.group(1)
        data = scrape_playlist(url, playlist_id)
    elif is_artist:
        artist_id = is_artist.group(1)
        data = scrape_artist(url, logger, config, artist_id)

    import json
    logger.out(json.dumps(data, indent=2), also_print=True)
    logger.out("TEST")


    return data

def scrape_artist(url, logger, config, artist_id):
    data = yt.get_artist(artist_id)
    
    data_artist = data["name"]
        
    def fetch_full_list(section_key):
        section = data.get(section_key)
        if not section: return []
        if 'browseId' in section:
            logger.out(f"Fetching full list for: {section_key}...")
            try:
                return yt.get_artist_albums(section['browseId'], section.get('params'))
            except Exception as e:
                logger.out(f"Error fetching {section_key}: {e}")
                return section.get('results', [])
        return section.get('results', [])

    full_albums = fetch_full_list("albums")
    full_singles = []
    if not config.get("artist_album_only", False):
        full_singles = fetch_full_list("singles")
        full_eps = fetch_full_list("ep")
        full_singles.extend(full_eps)

    logger.out(f"Processing {len(full_albums)} albums and {len(full_singles)} singles/EPs...")
        
    all_items = full_albums + full_singles
    data_albums = []
    for item in all_items:
        data_albums.append({
                "browseId": item["browseId"],
                "title": item.get("title", "Unknown Title"),
                "type": item.get("year", "Album")
            })

    data_cover_url = ""
    if 'thumbnails' in data and data['thumbnails']:
         data_cover_url = re.sub(r'w\d+-h\d+', "w1200-h1200", data['thumbnails'][0]['url'])

    data = {
            'url': url,
            'artist': data_artist,
            'cover': data_cover_url,
        }
    data["albums"] = data_albums
    data["albumCount"] = len(data_albums)
    data["type"] = "artist"

    logger.out(f"Found: {data['artist']}")
    logger.out(f"{data['albumCount']} releases found.")
    logger.out(f"\n".join(f"   {i}. {alb['title']}" for i, alb in enumerate(data['albums'][:10])))
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

def sanitise(s : str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '', s).strip()

def url_ready(s : str) -> str:
    return re.sub(r'[\"&/⧸#?:]', '', s.replace(" ", "+"))

def download_lyrics(track, artist_string, final_file_path, logger : Logger = Logger()):
    lrc_path = re.sub(r'\.mp3$', '.lrc', str(final_file_path))
    if os.path.exists(lrc_path):
        logger.out(f"Lyrics: {lrc_path} already exists...")
    else:
        logger.out(f"Downloading Lyrics: {lrc_path}")
        url = f"https://lrclib.net/api/get?artist_name={url_ready(artist_string)}&track_name={url_ready(track["title"])}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            if data.get("instrumental", False):
                with open(lrc_path, "w", encoding="utf-8") as f:
                    f.write("[00:00.00]♫")
            else:
                lyrics = data.get("syncedLyrics") or data.get("plainLyrics")
                with open(lrc_path, "w", encoding="utf-8") as f:
                    f.write(lyrics)

def download_track(track, data, config, cover_data, logger : Logger = Logger()) -> tuple[str, Path, str]:
    video_id : str = str(track["videoId"])
    local_yt_dlp = os.path.join(os.getcwd(), "yt-dlp.exe")
    temp_path = Path(config["temp_dir"])
    final_album_dir = Path(config["out_dir"]) / f"{sanitise(data['artist'])} - {sanitise(data['title'])}"

    artist_string = ", ".join(track['artists'])
    artist_tag_string = "; ".join(track['artists'])
    
    temp_filename = f"{video_id}.mp3"
    temp_file_path = temp_path / temp_filename
    
    final_filename = f"{sanitise(str(track['trackNumber']))}. {sanitise(artist_string)} - {sanitise(track['title'])}.mp3"
    final_file_path = final_album_dir / final_filename

    if final_file_path.exists():
        logger.out(f"Skipping (Exists): {track['title']}")
    else:
        cmd = [
            str(local_yt_dlp),
            "-x", "--audio-quality", "0",
            "--no-check-certificates",
            "-f", 'ba[acodec^=mp3]/ba/b',
            "--audio-format", "mp3",
            "--ffmpeg-location", os.getcwd(),
            "-o", os.path.join(temp_path, f"{track['videoId']}.%(ext)s"),
            f"https://www.youtube.com/watch?v={video_id}",
        ]

        startup_info = None
        if os.name == "nt":
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            logger.out(f"Downloading: {track['title']}")
            subprocess.run(cmd,  stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, startupinfo=startup_info,creationflags=0x08000000,)
            logger.out(f"Tagging: {track['title']}")

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
                logger.out(f"Tagging Error on {track['title']}: {e}")
            shutil.move(temp_file_path, final_file_path)

        except Exception as e:
            logger.out(f"Error processing {track['title']}: {e}")

    if config["download_lyrics"]:
        download_lyrics(track, artist_string, final_file_path, logger)
        
    logger.out(f"Finished: {track['title']}")
            
    return video_id, final_file_path, track['trackNumber']

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
    if cover_data is None: cover_data = get_album_cover(data["cover"], logger)
    save_album_cover(cover_data, data["artist"], data["title"], cover_path, logger)

    start_timer()

    with ThreadPoolExecutor(max_workers=config["max_threads"]) as executor:
        executor.map(lambda track: download_track(track, data, config, cover_data, logger), data["tracks"])

    stop_timer(logger=logger)

    logger.out(f"{'=' * 10}\nFINISHED DOWNLOADING ALBUM")

def download_playlist_album(i, track, config, logger = Logger()):
    global playlist_write_index
    # yt_album_data = yt.get_album(track["album_id"])
    album_data = scrape_album(album_id=track["album_id"], logger=logger)

    out_path = Path(config["out_dir"])
    cover_path = Path(config["cover_dir"])

    final_album_dir = out_path / f"{sanitise(album_data['artist'])} - {sanitise(album_data['title'])}"
    final_album_dir.mkdir(parents=True, exist_ok=True)

    logger.out(f"Starting Download: {album_data['artist']} - {album_data['title']}")
    cover_data = get_album_cover(album_data["cover"], logger)
    save_album_cover(cover_data, album_data["artist"], album_data["title"], cover_path, logger)

    playlsit_strings : list[str] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        r = executor.map(lambda track: download_track(track, album_data, config, cover_data, logger), album_data["tracks"])
        for video_id, final_file_path, trackNum in r:
            logger.out(f"{video_id} - {final_file_path}")
            if video_id in track["videoId"]:
                with file_lock:
                    playlsit_string = f"File{i}={final_file_path}\n"
                    playlist_write_index += 1
                playlsit_strings.append(playlsit_string)

    logger.out(str(playlsit_strings))
    return playlsit_strings

def download_album_by_id(album_id, config, logger = Logger()):
    cover_path = Path(config["cover_dir"])
    out_path = Path(config["out_dir"])

    album_data = scrape_data("", logger=logger, album_id=album_id)

    final_album_dir = out_path / f"{sanitise(album_data['artist'])} - {sanitise(album_data['title'])}"
    final_album_dir.mkdir(parents=True, exist_ok=True)

    logger.out(f"Starting Download: {album_data['artist']} - {album_data['title']}")
    cover_data = get_album_cover(album_data["cover"], logger)
    save_album_cover(cover_data, album_data["artist"], album_data["title"], cover_path, logger)

    with ThreadPoolExecutor(max_workers=4) as executor:
        executor.map(lambda track: download_track(track, album_data, config, cover_data, logger), album_data["tracks"])

def download_playlist(p_data, config, logger = Logger()):
    global playlist_write_index
    playlist_write_index = 1

    out_path = Path(config["out_dir"])
    cover_path = Path(config["cover_dir"])
    temp_path = Path(config["temp_dir"])
    out_path.mkdir(parents=True, exist_ok=True)
    cover_path.mkdir(parents=True, exist_ok=True)
    temp_path.mkdir(parents=True, exist_ok=True)

    playlsit_file_path = Path(out_path / f"{p_data["artist"]} - {p_data["title"]}.pls")

    playlsit_folder_path = Path(out_path / f"{p_data["artist"]} - {p_data["title"]}")
    playlsit_folder_path.mkdir(parents=True, exist_ok=True)

    for f in temp_path.glob("*"):
        try: f.unlink()
        except: pass

    playlsit_lines : list[str] = []

    start_timer()
    with ThreadPoolExecutor(max_workers=5) as executor:
        r = executor.map(lambda track: download_playlist_album(track[0], track[1], config, logger), [(i + 1, x) for i, x in enumerate(p_data["tracks"])])
        for strings in r:
            playlsit_lines.extend(strings)

    with open(playlsit_file_path, "w") as playlist_file:
        playlist_file.write("[playlist]\n")
        playlist_file.writelines(playlsit_lines)
        playlist_file.write(f"NumberOfEntries={len(p_data["tracks"])}\nVersion=2\n")

    stop_timer(logger=logger)

    logger.out(f"{'=' * 10}\nFINISHED DOWNLOADING PLAYLIST")
    logger.out(str(playlsit_lines))


def download_artist(artist_data, config, logger = Logger()):
    global playlist_write_index
    playlist_write_index = 0

    out_path = Path(config["out_dir"])
    cover_path = Path(config["cover_dir"])
    temp_path = Path(config["temp_dir"])

    out_path.mkdir(parents=True, exist_ok=True)
    cover_path.mkdir(parents=True, exist_ok=True)
    temp_path.mkdir(parents=True, exist_ok=True)

    for f in temp_path.glob("*"):
        try: f.unlink()
        except: pass

    album_ids = [album["browseId"] for album in artist_data["albums"]]

    start_timer()

    with ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(lambda album_id: download_album_by_id(album_id, config, logger), album_ids)

    stop_timer(logger=logger)

    logger.out(f"{'=' * 10}\nFINISHED DOWNLOADING ARTIST")

class Worker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal()
    data_signal = Signal(dict, bytes)

    def __init__(self, urls, config, data_only=False, direct_data=None):
        super().__init__()
        self.urls = urls if isinstance(urls, list) else [urls]
        self.config = config
        self.data_only = data_only
        self.direct_data = direct_data

    def run(self):
        logger = Logger(logger=self.log_signal.emit)
        
        if self.direct_data:
            logger.out("Processing selected items...")
            data = self.direct_data
            
            self.data_signal.emit(data, b'') 

            if not self.data_only:
                if data["type"] == "playlist":
                    download_playlist(p_data=data, config=self.config, logger=logger)
                elif data["type"] == "artist":
                    download_artist(artist_data=data, config=self.config, logger=logger)
                else:
                    download_album(data, self.config, logger=logger)
        else:
            for i, url in enumerate(self.urls):
                if not url.strip(): continue
                if len(self.urls) > 1:
                    logger.out(f"{'='*10}\nProcessing URL {i+1}/{len(self.urls)}")
                
                # data = scrape_data(url, logger=logger, config=self.config)
                data = new_scrape_data(url=url, logger=logger, config=self.config)
                    
                if data:
                    cover_data = get_album_cover(data["cover"], logger=logger)
                    self.data_signal.emit(data, cover_data if cover_data else b'')
                    if not self.data_only:
                        if data["type"] == "playlist":
                            download_playlist(p_data=data, config=self.config, logger=logger)
                        elif data["type"] == "artist":
                            download_artist(artist_data=data, config=self.config, logger=logger)
                        else:
                            download_album(data, self.config, logger=logger, cover_data=cover_data)
                else:
                    logger.out(f"Skipping Invalid URL: {url}")

        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        self.finished_signal.emit()

class MusicDownloaderGUI(QWidget):
    def __init__(self, config):
        super().__init__()
        self.setWindowTitle("Music Downloader")
        self.resize(1400, 700)

        self.config = config
        self.current_fetched_data = None
        self.setup_ui()

    def setup_ui(self):
        main_layout = QHBoxLayout(self)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        form_layout = QFormLayout()

        self.batch_mode_checkbox = QCheckBox("Batch Mode")
        self.batch_mode_checkbox.toggled.connect(self.toggle_mode)
        
        self.btn_clear_data = QPushButton("Clear Data")
        self.btn_clear_data.setFixedWidth(120)
        self.btn_clear_data.clicked.connect(lambda: self.reset_data)

        batch_clear_widget = QHBoxLayout()
        batch_clear_widget.addWidget(self.batch_mode_checkbox)
        batch_clear_widget.addWidget(self.btn_clear_data)

        form_layout.addRow(batch_clear_widget)

        self.single_url_input = QLineEdit()
        self.single_url_input.setPlaceholderText("Paste URL here...")
        self.single_url_input.textChanged.connect(self.reset_data)
        self.batch_url_input = QTextEdit()
        self.batch_url_input.setPlaceholderText("Paste URLs here (one per line)...")
        self.batch_url_input.setFixedHeight(100)
        self.batch_url_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.batch_url_input.setVisible(False)

        self.btn_fetch_data = QPushButton("Fetch Data")
        self.btn_fetch_data.setFixedWidth(120)
        self.btn_fetch_data.clicked.connect(lambda: self.fetch_data())
        
        url_container = QVBoxLayout()
        url_container.addWidget(self.single_url_input)
        url_container.addWidget(self.batch_url_input)

        input_row_layout = QHBoxLayout()
        input_row_layout.addLayout(url_container)
        input_row_layout.addWidget(self.btn_fetch_data)
        
        form_layout.addRow("URL:", input_row_layout)

        self.out_input = QLineEdit(self.config["out_dir"])
        self.btn_browse_out = QPushButton("...")
        self.btn_browse_out.clicked.connect(lambda: self.browse_folder(self.out_input))
        out_layout = QHBoxLayout()
        out_layout.addWidget(self.out_input)
        out_layout.addWidget(self.btn_browse_out)
        form_layout.addRow("Output Path:", out_layout)

        self.temp_input = QLineEdit(self.config["temp_dir"])
        self.btn_browse_temp = QPushButton("...")
        self.btn_browse_temp.clicked.connect(lambda: self.browse_folder(self.temp_input))
        temp_layout = QHBoxLayout()
        temp_layout.addWidget(self.temp_input)
        temp_layout.addWidget(self.btn_browse_temp)
        form_layout.addRow("Temp Path:", temp_layout)

        self.cover_input = QLineEdit(self.config["cover_dir"])
        self.btn_browse_cover = QPushButton("...")
        self.btn_browse_cover.clicked.connect(lambda: self.browse_folder(self.cover_input))
        cover_layout = QHBoxLayout()
        cover_layout.addWidget(self.cover_input)
        cover_layout.addWidget(self.btn_browse_cover)
        form_layout.addRow("Cover Path:", cover_layout)

        self.num_threads_input = QSpinBox()
        self.num_threads_input.setRange(1, 128)
        self.num_threads_input.setValue(int(self.config.get("max_threads", 4)))
        self.num_threads_input.setFixedWidth(80)
        form_layout.addRow("Max Threads:", self.num_threads_input)

        self.lyrics_checkbox = QCheckBox()
        self.lyrics_checkbox.setChecked(self.config["download_lyrics"])
        lyric_container_layout = QHBoxLayout()
        lyric_container_layout.addWidget(QLabel("Download Lyrics:"))
        lyric_container_layout.addWidget(self.lyrics_checkbox)
        lyric_container_layout.addStretch()
        form_layout.addRow(lyric_container_layout)
        # form_layout.addRow("Download Lyrics:", self.lyrics_checkbox)

        self.artist_albums_checkbox = QCheckBox()
        self.artist_albums_checkbox.setChecked(self.config["artist_album_only"])
        container_layout = QHBoxLayout()
        container_layout.addWidget(QLabel("[ARTISTS] Download ONLY albums:"))
        container_layout.addWidget(self.artist_albums_checkbox)
        container_layout.addStretch()
        form_layout.addRow(container_layout)

        left_layout.addLayout(form_layout)

        self.start_btn = QPushButton("Start Download")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self.start_process)
        left_layout.addWidget(self.start_btn)

        self.console_label = QLabel("Console Output:")
        left_layout.addWidget(self.console_label)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet("background-color: #222; color: #EEE; font-family: Consolas, monospace;")
        left_layout.addWidget(self.console)

        central_widget = QWidget()
        central_widget.setFixedWidth(320)
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(10, 0, 0, 0)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(300, 300)
        self.cover_label.setStyleSheet("background-color: #333; border: 1px solid #555;")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setText("No Cover")
        central_layout.addWidget(self.cover_label)

        self.info_console = QTextEdit()
        self.info_console.setReadOnly(True)
        self.info_console.setStyleSheet("background-color: #222; color: #EEE; font-family: Consolas, monospace;")
        self.info_console.setFixedWidth(300)
        central_layout.addWidget(self.info_console)

        right_widget = QWidget()
        right_widget.setFixedWidth(400)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        self.selection_label = QLabel("Select Items to Download:")
        right_layout.addWidget(self.selection_label)

        self.item_list = QListWidget()
        self.item_list.setStyleSheet("background-color: #222; color: #EEE;")
        right_layout.addWidget(self.item_list)

        main_layout.addWidget(left_widget, 1) 
        main_layout.addWidget(central_widget, 0)
        main_layout.addWidget(right_widget, 0)

    def reset_data(self):
        self.current_fetched_data = None
        self.item_list.clear()
        self.console.clear()
        self.cover_label.clear()
        self.cover_label.setText("No Cover")
        self.info_console.clear()

    def toggle_mode(self, checked):
        if checked:
            self.single_url_input.setVisible(False)
            self.btn_fetch_data.setVisible(False)
            self.batch_url_input.setVisible(True)
            self.item_list.setEnabled(False)
            self.selection_label.setEnabled(False)
        else:
            self.single_url_input.setVisible(True)
            self.btn_fetch_data.setVisible(True)
            self.batch_url_input.setVisible(False)
            self.item_list.setEnabled(True)
            self.selection_label.setEnabled(True)

    def get_urls(self):
        if self.batch_mode_checkbox.isChecked():
            text = self.batch_url_input.toPlainText()
            return [line.strip() for line in text.splitlines() if line.strip()]
        else:
            url = self.single_url_input.text().strip()
            return [url] if url else []

    def fetch_data(self):
        urls = self.get_urls()
        if not urls:
            self.log_to_console("Error: Please enter a URL.")
            return
        
        config = self.parse_config()
        self.btn_fetch_data.setEnabled(False)
        self.console.clear()
        self.item_list.clear()
        
        self.worker = Worker(urls, config, data_only=True)
        self.worker.log_signal.connect(self.log_to_console)
        self.worker.data_signal.connect(self.update_info_panel)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def start_process(self):
        urls = self.get_urls()
        if not urls:
            self.log_to_console("Error: Please enter a URL.")
            return

        config = self.parse_config()
        
        final_data = None
        if not self.batch_mode_checkbox.isChecked() and self.current_fetched_data:
            final_data = deepcopy(self.current_fetched_data)
            
            checked_ids = set()
            for i in range(self.item_list.count()):
                item = self.item_list.item(i)
                if item.checkState() == Qt.Checked:
                    checked_ids.add(item.data(Qt.UserRole))
            
            if final_data["type"] == "artist":
                original_len = len(final_data["albums"])
                final_data["albums"] = [a for a in final_data["albums"] if a["browseId"] in checked_ids]
                self.log_to_console(f"Filtered: {len(final_data['albums'])} / {original_len} albums selected.")
            else:
                original_len = len(final_data["tracks"])
                final_data["tracks"] = [t for t in final_data["tracks"] if t.get("videoId") in checked_ids]
                if "videoIds" in final_data:
                    final_data["videoIds"] = [v for v in final_data["videoIds"] if v in checked_ids]
                self.log_to_console(f"Filtered: {len(final_data['tracks'])} / {original_len} tracks selected.")

        self.btn_fetch_data.setEnabled(False)
        self.lyrics_checkbox.setEnabled(False)
        self.artist_albums_checkbox.setEnabled(False)
        self.batch_mode_checkbox.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.btn_clear_data.setEnabled(False)
        self.start_btn.setText("Processing...")
        
        if not final_data:
            self.console.clear()
            self.cover_label.clear()
            self.cover_label.setText("Loading...")

        self.worker = Worker(urls, config, direct_data=final_data)
        self.worker.log_signal.connect(self.log_to_console)

        if not final_data:
            self.worker.data_signal.connect(self.update_info_panel)
            
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def update_info_panel(self, data, cover_data):
        self.current_fetched_data = data
        
        style_key = "font-weight: bold; color: #FFD700;" 
        style_val = "color: #FFFFFF;"
        
        info_text = ""
        self.item_list.clear()

        if data["type"] == "artist":
            list_html = "<br>".join(
                f"<span style='font-weight: bold; color: #FFFFFF;'>{i}. </span>"
                f"<span style='color: #FFFFFF;'>{album['title']} - {album['type']}</span>"
                for i, album in enumerate(data["albums"]))
            
            info_text = (
                f"<span style='{style_key}'>Artist:</span> <span style='{style_val}'>{data.get('artist', 'Unknown')}</span><br>"
                f"<span style='{style_key}'>Items:</span> <span style='{style_val}'>{data.get('albumCount', 0)}</span>"
                f"<div style='margin-left: 1em;'>{list_html}</div>"
            )

            for album in data["albums"]:
                item = QListWidgetItem(f"{album['title']} ({album['type']})")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setData(Qt.UserRole, album['browseId'])
                self.item_list.addItem(item)
        elif data["type"] == "album":
            list_html = "<br>".join(
                f"<span style='font-weight: bold; color: #FFFFFF;'>{track['trackNumber']}. </span>"
                f"<span style='color: #FFFFFF;'>{track['title']}</span>"
                for track in data["tracks"]) if data["type"] != "playlist" else "<br>".join(
                            f"<span style='color: #FFFFFF;'>{track['title']}</span>"
                            for track in data["tracks"])

            info_text = (
                f"<span style='{style_key}'>Title:</span> <span style='{style_val}'>{data.get('title', 'Unknown')}</span><br>"
                f"<span style='{style_key}'>Artist:</span> <span style='{style_val}'>{data.get('artist', 'Unknown')}</span><br>"
                f"<span style='{style_key}'>Year:</span> <span style='{style_val}'>{data.get('year', 'Unknown')}</span><br>"
                f"<span style='{style_key}'>Type:</span> <span style='{style_val}'>{data.get('type', 'Unknown').capitalize()}</span><br>"
                f"<span style='{style_key}'>Tracks:</span> <span style='{style_val}'>{data.get('trackcount', 0)}</span>"
                f"<div style='margin-left: 1em;'>{list_html}</div>"
            )

            for track in data["tracks"]:
                item = QListWidgetItem(f"{track['trackNumber']}. {track['title']}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setData(Qt.UserRole, track.get('videoId'))
                self.item_list.addItem(item)
        elif data["type"] == "playlist":
            list_html = "<br>".join(
                f"<span style='font-weight: bold; color: #FFFFFF;'>{track['trackNumber']}. </span>"
                f"<span style='color: #FFFFFF;'>{track['title']}</span>"
                for track in data["tracks"]) if data["type"] != "playlist" else "<br>".join(
                            f"<span style='color: #FFFFFF;'>{track['title']}</span>"
                            for track in data["tracks"])

            info_text = (
                f"<span style='{style_key}'>Title:</span> <span style='{style_val}'>{data.get('title', 'Unknown')}</span><br>"
                f"<span style='{style_key}'>Artist:</span> <span style='{style_val}'>{data.get('artist', 'Unknown')}</span><br>"
                f"<span style='{style_key}'>Year:</span> <span style='{style_val}'>{data.get('year', 'Unknown')}</span><br>"
                f"<span style='{style_key}'>Type:</span> <span style='{style_val}'>{data.get('type', 'Unknown').capitalize()}</span><br>"
                f"<span style='{style_key}'>Tracks:</span> <span style='{style_val}'>{data.get('trackcount', 0)}</span>"
                f"<div style='margin-left: 1em;'>{list_html}</div>"
            )

            for track_num, track in enumerate(data["tracks"]):
                item = QListWidgetItem(f"{track_num}. {track['title']}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setData(Qt.UserRole, track.get('videoId'))
                self.item_list.addItem(item)

        self.info_console.setHtml(info_text)

        if cover_data:
            pixmap = QPixmap()
            pixmap.loadFromData(cover_data)
            self.cover_label.setPixmap(pixmap.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.cover_label.setText("No Cover Found")

    def on_finished(self):
        self.lyrics_checkbox.setEnabled(True)
        self.btn_fetch_data.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.artist_albums_checkbox.setEnabled(True)
        self.batch_mode_checkbox.setEnabled(True)
        self.btn_clear_data.setEnabled(True)
        self.start_btn.setText("Start Download")

    def browse_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "Select Directory", line_edit.text())
        if folder:
            line_edit.setText(folder)

    def log_to_console(self, text):
        self.console.append(text)
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def parse_config(self):
        config = self.config.copy()
        config["out_dir"] = self.out_input.text()
        config["temp_dir"] = self.temp_input.text()
        config["cover_dir"] = self.cover_input.text()
        config["max_threads"] = self.num_threads_input.value()
        config["download_lyrics"] = self.lyrics_checkbox.isChecked()
        config["artist_album_only"] = self.artist_albums_checkbox.isChecked()
        return config

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
        url = args.ytb_url
        logger = Logger(print if args.verbose else None)

        url.strip()
        data = new_scrape_data(url=url, logger=logger, config=config)
            
        if data:
            cover_data = get_album_cover(data["cover"], logger=logger)
            if data["type"] == "playlist":
                download_playlist(p_data=data, config=config, logger=logger)
            elif data["type"] == "artist":
                download_artist(artist_data=data, config=config, logger=logger)
            else:
                download_album(data, config, logger=logger, cover_data=cover_data)
            if args.verbose: print(f"Download Finished!")
        else:
            logger.out(f"Skipping Invalid URL: {url}")

    else:
        if os.name == 'nt':
            myappid = 'music.downloader.gui.v1'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        app = QApplication(sys.argv)
        # qt_material.apply_stylesheet(app, theme="dark_purple.xml", style="windows11")        
        if os.path.exists("MusicDownloader.ico"):
            app.setWindowIcon(QIcon("MusicDownloader.ico"))
        window = MusicDownloaderGUI(config)
        window.show()
        sys.exit(app.exec())