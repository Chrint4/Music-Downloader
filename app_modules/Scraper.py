import os, re
import requests
from ytmusicapi import YTMusic
import yt_dlp

from Logger import Logger

class Scraper():

    @staticmethod
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

    @staticmethod
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
        # logger.out(f"data_tracks: {data_tracks}")
        # logger.out(f"ytmusicdata: {ytmusicapi_data.get('tracks', [])}")
        # logger.out(f"ytdlpdata: {ytdlp_data}")

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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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