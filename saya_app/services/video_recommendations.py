import hashlib
import html
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlencode, urlparse
from xml.etree import ElementTree

import requests

from saya_app.config import (
    BILIBILI_COOKIE_FILE,
    KNOWLEDGE_BASE_DIR,
    YOUTUBE_API_KEY_FILE,
    YOUTUBE_COOKIE_FILE,
)


class BilibiliRateLimitError(RuntimeError):
    pass


class VideoRecommendationService:
    RECENT_WINDOW_DAYS = 3
    CACHE_RETENTION_DAYS = 14
    BILIBILI_BACKOFF_MINUTES = 30
    BILIBILI_WBI_KEY_TABLE = [
        46, 47, 18, 2, 53, 8, 23, 32,
        15, 50, 10, 31, 58, 3, 45, 35,
        27, 43, 5, 49, 33, 9, 42, 19,
        29, 28, 14, 39, 12, 38, 41, 13,
        37, 48, 7, 16, 24, 55, 40, 61,
        26, 17, 0, 1, 60, 51, 30, 4,
        22, 25, 54, 21, 56, 59, 6, 63,
        57, 62, 11, 36, 20, 34, 44, 52,
    ]

    def __init__(self, file_path=None):
        self.file_path = Path(file_path or (KNOWLEDGE_BASE_DIR / "video_recommendations.json"))
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self._load_warning = None
        self.data = self._load()
        if self._load_warning:
            self._save()
        self._bilibili_mixin_key = None
        self._bilibili_mixin_key_expires_at = None

    def _default_data(self):
        return {
            "schema_version": 1,
            "settings": {
                "auto_open": False,
                "cache_ttl_minutes": 60,
                "max_results": 24,
                "recent_days": self.RECENT_WINDOW_DAYS,
            },
            "creators": [],
            "watched_videos": {},
            "cache": {
                "last_checked": None,
                "videos": [],
            },
        }

    def _load(self):
        if not self.file_path.exists():
            return self._default_data()
        raw_text = ""
        try:
            raw_text = self.file_path.read_text(encoding="utf-8")
            loaded = json.loads(raw_text)
        except Exception as exc:
            print(f"Error loading video recommendations: {exc}")
            loaded = self._recover_partial_json_data(raw_text)
            if loaded:
                self._load_warning = "Recovered settings/creators from a damaged video recommendations file."
                self._backup_damaged_state_file(raw_text)
            else:
                self._load_warning = "Video recommendations file was damaged and could not be recovered."
        return self._normalize_loaded_data(loaded)

    def _normalize_loaded_data(self, loaded):
        defaults = self._default_data()
        if not isinstance(loaded, dict):
            return defaults

        defaults.update(loaded)
        settings = defaults.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        defaults["settings"] = {**self._default_data()["settings"], **settings}

        creators = []
        if isinstance(defaults.get("creators"), list):
            for creator in defaults.get("creators", []):
                if not isinstance(creator, dict):
                    continue
                if (
                    creator.get("platform") == "bilibili"
                    and creator.get("status") == "error"
                    and "Bilibili video list request failed" in str(creator.get("last_error", ""))
                ):
                    creator["status"] = "limited"
                    creator["last_error"] = "Bilibili public API was rate limited; using cached results."
                creators.append(creator)
        defaults["creators"] = creators

        if not isinstance(defaults.get("watched_videos"), dict):
            defaults["watched_videos"] = {}
        if not isinstance(defaults.get("cache"), dict):
            defaults["cache"] = self._default_data()["cache"]
        if not isinstance(defaults["cache"].get("videos"), list):
            defaults["cache"]["videos"] = []
        return defaults

    def _recover_partial_json_data(self, raw_text):
        if not raw_text:
            return None
        recovered = {}
        for key in ("settings", "creators", "watched_videos"):
            value = self._extract_json_member(raw_text, key)
            if value is not None:
                recovered[key] = value
        last_checked = self._extract_cache_last_checked(raw_text)
        if last_checked:
            recovered["cache"] = {
                "last_checked": last_checked,
                "videos": [],
                "last_refresh_had_results": False,
            }
        return recovered or None

    def _extract_json_member(self, raw_text, key):
        match = re.search(rf'"{re.escape(key)}"\s*:', raw_text)
        if not match:
            return None
        start = match.end()
        while start < len(raw_text) and raw_text[start].isspace():
            start += 1
        end = self._json_value_end(raw_text, start)
        if end is None:
            return None
        try:
            return json.loads(raw_text[start:end])
        except Exception:
            return None

    def _json_value_end(self, text, start):
        if start >= len(text):
            return None
        opener = text[start]
        if opener in "{[":
            pairs = {"{": "}", "[": "]"}
            stack = []
            in_string = False
            escaped = False
            for index in range(start, len(text)):
                char = text[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char in pairs:
                    stack.append(pairs[char])
                elif stack and char == stack[-1]:
                    stack.pop()
                    if not stack:
                        return index + 1
            return None
        if opener == '"':
            escaped = False
            for index in range(start + 1, len(text)):
                char = text[index]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    return index + 1
            return None
        match = re.search(r"[,}\]\r\n]", text[start:])
        return start + match.start() if match else len(text)

    def _extract_cache_last_checked(self, raw_text):
        match = re.search(r'"last_checked"\s*:\s*"([^"]+)"', raw_text)
        return match.group(1) if match else None

    def _backup_damaged_state_file(self, raw_text):
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            backup_path = self.file_path.with_name(f"{self.file_path.name}.corrupt-{stamp}.bak")
            backup_path.write_text(raw_text, encoding="utf-8")
        except Exception as exc:
            print(f"Error backing up damaged video recommendations file: {exc}")

    def _save(self):
        temp_path = self.file_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as target:
            json.dump(self.data, target, ensure_ascii=False, indent=4)
        os.replace(temp_path, self.file_path)

    def get_state(self):
        bilibili_cookie, bilibili_cookie_source = self._bilibili_cookie_config()
        youtube_cookie, youtube_cookie_source = self._youtube_cookie_config()
        youtube_api_key, youtube_api_key_source = self._youtube_api_key_config()
        return {
            "settings": self.data.get("settings", {}),
            "creators": self.data.get("creators", []),
            "last_checked": self.data.get("cache", {}).get("last_checked"),
            "recommendations": self._current_recommendations(),
            "management_videos": self._current_recommendations(include_watched=True),
            "watched_count": len(self.data.get("watched_videos", {})),
            "window_days": self._recent_days(),
            "bilibili_cookie_configured": bool(bilibili_cookie),
            "bilibili_cookie_source": bilibili_cookie_source,
            "youtube_cookie_configured": bool(youtube_cookie),
            "youtube_cookie_source": youtube_cookie_source,
            "youtube_api_key_configured": bool(youtube_api_key),
            "youtube_api_key_source": youtube_api_key_source,
            "state_warning": self._load_warning,
        }

    def update_settings(self, updates):
        settings = self.data.setdefault("settings", self._default_data()["settings"])
        if "auto_open" in updates:
            settings["auto_open"] = bool(updates.get("auto_open"))
        if "cache_ttl_minutes" in updates:
            settings["cache_ttl_minutes"] = self._coerce_int(updates.get("cache_ttl_minutes"), 60, 5, 720)
        if "max_results" in updates:
            settings["max_results"] = self._coerce_int(updates.get("max_results"), 24, 6, 60)
        self._save()
        return settings

    def add_creator(self, platform, name):
        platform = self._normalize_platform(platform)
        name = re.sub(r"\s+", " ", str(name or "")).strip()
        if platform not in {"youtube", "bilibili"}:
            raise ValueError("Platform must be youtube or bilibili")
        if not name:
            raise ValueError("Creator name is required")

        normalized = name.lower()
        for creator in self.data.setdefault("creators", []):
            if creator.get("platform") == platform and str(creator.get("name", "")).lower() == normalized:
                return creator

        creator = {
            "id": self._stable_id(platform, name),
            "platform": platform,
            "name": name,
            "display_name": name,
            "enabled": True,
            "source_id": None,
            "url": None,
            "status": "pending",
            "last_error": None,
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
        }
        self.data["creators"].append(creator)
        self._resolve_creator_best_effort(creator)
        self._save()
        return creator

    def delete_creator(self, creator_id):
        creators = self.data.setdefault("creators", [])
        next_creators = [creator for creator in creators if creator.get("id") != creator_id]
        changed = len(next_creators) != len(creators)
        self.data["creators"] = next_creators
        if changed:
            self._save()
        return changed

    def get_recommendations(self, refresh=False):
        if not refresh and not self._cache_expired():
            return self.get_state()

        cached_videos = list(self.data.get("cache", {}).get("videos", []))
        fetched_videos = []
        bilibili_cookie_configured = self._bilibili_cookie_configured()
        for creator in self.data.get("creators", []):
            if not creator.get("enabled", True):
                continue
            if self._creator_refresh_blocked(creator):
                continue
            if not creator.get("source_id"):
                self._resolve_creator_best_effort(creator)
            try:
                if creator.get("source_id"):
                    fetched = self._fetch_creator_videos(creator)
                    fetched_videos.extend(fetched)
                    creator["status"] = "ok"
                    creator["last_error"] = None
                    creator.pop("next_refresh_at", None)
                    creator.pop("rate_limited_with_cookie", None)
                else:
                    creator["status"] = "unresolved"
            except BilibiliRateLimitError as exc:
                creator["status"] = "limited"
                creator["last_error"] = str(exc)
                creator["next_refresh_at"] = (
                    datetime.now(timezone.utc) + timedelta(minutes=self.BILIBILI_BACKOFF_MINUTES)
                ).isoformat()
                creator["rate_limited_with_cookie"] = bilibili_cookie_configured
            except Exception as exc:
                creator["status"] = "error"
                creator["last_error"] = str(exc)[:300]
            creator["updated_at"] = self._now_iso()

        videos = self._prune_cache_videos(self._rank_and_dedupe(fetched_videos + cached_videos))
        cache_limit = max(60, self.data.get("settings", {}).get("max_results", 24) * 4)
        self.data["cache"] = {
            "last_checked": self._now_iso(),
            "videos": videos[:cache_limit],
            "last_refresh_had_results": bool(fetched_videos),
        }
        self._prune_watched_videos()
        self._save()
        return self.get_state()

    def _creator_refresh_blocked(self, creator):
        next_refresh_at = self._parse_datetime(creator.get("next_refresh_at"))
        if not next_refresh_at:
            return False
        if (
            creator.get("platform") == "bilibili"
            and self._bilibili_cookie_configured()
            and not creator.get("rate_limited_with_cookie")
        ):
            creator.pop("next_refresh_at", None)
            if creator.get("status") == "limited":
                creator["status"] = "pending"
            return False
        now = datetime.now(timezone.utc)
        if next_refresh_at <= now:
            creator.pop("next_refresh_at", None)
            if creator.get("status") == "limited":
                creator["status"] = "pending"
            return False
        creator["status"] = "limited"
        creator["last_error"] = f"Bilibili rate limited; using cached results until {next_refresh_at.isoformat()}"
        return True

    def set_video_watched(self, video_id, watched=True, extra=None):
        video_id = str(video_id or "").strip()
        if not video_id:
            raise ValueError("Video id is required")

        self._set_watched_record(video_id, watched=watched, extra=extra)
        self._prune_watched_videos()
        self._save()
        return self.get_state()

    def prepare_video_discussion(self, video_id):
        video_id = str(video_id or "").strip()
        if not video_id:
            raise ValueError("Video id is required")
        video = self._find_cached_video(video_id)
        if not video:
            raise ValueError("Video not found in cache")

        context = self._fetch_video_discussion_context(video)
        prompt = self._build_discussion_prompt(video, context)
        self._set_watched_record(video_id, watched=True, extra={
            "discussion_started_at": self._now_iso(),
        })
        self._prune_watched_videos()
        self._save()
        return {
            "status": "success",
            "prompt": prompt,
            "display_text": f"Doctor给你分享了一个视频：{video.get('title') or 'Untitled'}",
            "video": self._with_watched_state(video),
            "context": context,
            "state": self.get_state(),
        }

    def _set_watched_record(self, video_id, watched=True, extra=None):
        watched_videos = self.data.setdefault("watched_videos", {})
        if watched:
            video = self._find_cached_video(video_id) or {}
            record = {
                "watched_at": self._now_iso(),
                "title": video.get("title"),
                "url": video.get("url"),
                "creator_name": video.get("creator_name"),
            }
            if extra:
                record.update(extra)
            watched_videos[video_id] = record
        else:
            watched_videos.pop(video_id, None)

    def _current_recommendations(self, include_watched=False):
        active_creator_ids = {
            creator.get("id")
            for creator in self.data.get("creators", [])
            if creator.get("enabled", True)
        }
        watched_videos = self.data.get("watched_videos", {})
        limit = self.data.get("settings", {}).get("max_results", 24)
        videos = []
        for video in self._rank_and_dedupe(self.data.get("cache", {}).get("videos", [])):
            if video.get("creator_id") not in active_creator_ids:
                continue
            if not include_watched and video.get("id") in watched_videos:
                continue
            if not self._is_recent_video(video):
                continue
            videos.append(self._with_watched_state(video))
            if len(videos) >= limit:
                break
        return videos

    def _with_watched_state(self, video):
        item = dict(video)
        watched = self.data.get("watched_videos", {}).get(video.get("id"))
        item["watched"] = bool(watched)
        if watched:
            item["watched_at"] = watched.get("watched_at")
        return item

    def _find_cached_video(self, video_id):
        for video in self.data.get("cache", {}).get("videos", []):
            if video.get("id") == video_id:
                return video
        return None

    def _recent_days(self):
        return self._coerce_int(
            self.data.get("settings", {}).get("recent_days", self.RECENT_WINDOW_DAYS),
            self.RECENT_WINDOW_DAYS,
            1,
            30,
        )

    def _is_recent_video(self, video):
        age_days = self._age_days(video.get("published_at"))
        return age_days is not None and age_days <= self._recent_days()

    def _prune_cache_videos(self, videos):
        active_creator_ids = {
            creator.get("id")
            for creator in self.data.get("creators", [])
            if creator.get("enabled", True)
        }
        clean = []
        for video in videos:
            if video.get("creator_id") not in active_creator_ids:
                continue
            age_days = self._age_days(video.get("published_at"))
            if age_days is None or age_days <= self.CACHE_RETENTION_DAYS:
                clean.append(video)
        return clean

    def _prune_watched_videos(self):
        watched_videos = self.data.setdefault("watched_videos", {})
        for video_id, record in list(watched_videos.items()):
            watched_at = self._parse_datetime(record.get("watched_at") if isinstance(record, dict) else None)
            if watched_at is None:
                continue
            age_days = (datetime.now(timezone.utc) - watched_at).total_seconds() / 86400
            if age_days > 180:
                watched_videos.pop(video_id, None)

    def _resolve_creator_best_effort(self, creator):
        try:
            if creator["platform"] == "youtube":
                resolved = self._resolve_youtube_creator(creator["name"])
            else:
                resolved = self._resolve_bilibili_creator(creator["name"])
            if resolved:
                creator.update(resolved)
                creator["status"] = "ok"
                creator["last_error"] = None
            else:
                creator["status"] = "unresolved"
                creator["last_error"] = "Creator could not be resolved yet."
        except Exception as exc:
            creator["status"] = "error"
            creator["last_error"] = str(exc)[:300]
        creator["updated_at"] = self._now_iso()

    def _fetch_creator_videos(self, creator):
        if creator["platform"] == "youtube":
            return self._fetch_youtube_videos(creator)
        if creator["platform"] == "bilibili":
            return self._fetch_bilibili_videos(creator)
        return []

    def _resolve_youtube_creator(self, value):
        channel_id = self._extract_youtube_channel_id(value)
        api_key, _ = self._youtube_api_key_config()
        if channel_id:
            if api_key:
                resolved = self._fetch_youtube_channel_by_id(channel_id, api_key)
                if resolved:
                    return resolved
            return {
                "source_id": channel_id,
                "url": f"https://www.youtube.com/channel/{channel_id}",
            }

        api_error = None
        if api_key:
            try:
                resolved = self._resolve_youtube_creator_with_api(value, api_key)
                if resolved:
                    return resolved
            except Exception as exc:
                api_error = str(exc)[:180]

        candidates = self._youtube_handle_candidates(value)
        for handle in candidates:
            try:
                html = self._get_text(f"https://www.youtube.com/@{handle}")
                channel_id = self._extract_youtube_channel_id(html)
                if channel_id:
                    display_name = self._extract_youtube_title(html) or value
                    return {
                        "source_id": channel_id,
                        "display_name": display_name,
                        "url": f"https://www.youtube.com/@{handle}",
                    }
            except Exception as exc:
                api_error = api_error or str(exc)[:180]

        search_url = f"https://www.youtube.com/results?search_query={quote_plus(value)}&sp=EgIQAg%253D%253D"
        try:
            html = self._get_text(search_url)
            channel_id = self._extract_youtube_channel_id(html)
            if channel_id:
                display_name = self._extract_youtube_title(html) or value
                return {
                    "source_id": channel_id,
                    "display_name": display_name,
                    "url": f"https://www.youtube.com/channel/{channel_id}",
                }
        except Exception as exc:
            api_error = api_error or str(exc)[:180]
        if api_error:
            raise RuntimeError(f"YouTube creator could not be resolved: {api_error}")
        return None

    def _fetch_youtube_videos(self, creator):
        channel_id = creator.get("source_id")
        api_key, _ = self._youtube_api_key_config()
        api_error = None
        if api_key:
            try:
                return self._fetch_youtube_api_videos(creator, api_key)
            except Exception as exc:
                api_error = str(exc)[:180]
        try:
            return self._fetch_youtube_feed_videos(creator, channel_id)
        except Exception as exc:
            if api_error:
                raise RuntimeError(f"YouTube API and RSS fetch both failed: API={api_error}; RSS={str(exc)[:180]}")
            raise

    def _resolve_youtube_creator_with_api(self, value, api_key):
        for handle in self._youtube_handle_candidates(value):
            resolved = self._fetch_youtube_channel_by_handle(handle, api_key)
            if resolved:
                return resolved
            resolved = self._fetch_youtube_channel_by_username(handle, api_key)
            if resolved:
                return resolved

        payload = self._get_json(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": str(value or "").strip(),
                "type": "channel",
                "maxResults": 1,
                "key": api_key,
            },
            referer="https://www.youtube.com/",
            headers={"Accept": "application/json"},
        )
        items = payload.get("items", []) if isinstance(payload, dict) else []
        for item in items:
            channel_id = item.get("id", {}).get("channelId")
            if channel_id:
                return self._fetch_youtube_channel_by_id(channel_id, api_key) or {
                    "source_id": channel_id,
                    "display_name": item.get("snippet", {}).get("channelTitle") or value,
                    "url": f"https://www.youtube.com/channel/{channel_id}",
                }
        return None

    def _fetch_youtube_channel_by_id(self, channel_id, api_key):
        if not channel_id:
            return None
        payload = self._get_json(
            "https://www.googleapis.com/youtube/v3/channels",
            params={
                "part": "snippet,contentDetails",
                "id": channel_id,
                "key": api_key,
            },
            referer="https://www.youtube.com/",
            headers={"Accept": "application/json"},
        )
        return self._youtube_channel_result(payload)

    def _fetch_youtube_channel_by_handle(self, handle, api_key):
        handle = str(handle or "").strip().lstrip("@")
        if not handle:
            return None
        payload = self._get_json(
            "https://www.googleapis.com/youtube/v3/channels",
            params={
                "part": "snippet,contentDetails",
                "forHandle": handle,
                "key": api_key,
            },
            referer="https://www.youtube.com/",
            headers={"Accept": "application/json"},
        )
        return self._youtube_channel_result(payload, fallback_url=f"https://www.youtube.com/@{handle}")

    def _fetch_youtube_channel_by_username(self, username, api_key):
        username = str(username or "").strip().lstrip("@")
        if not username:
            return None
        payload = self._get_json(
            "https://www.googleapis.com/youtube/v3/channels",
            params={
                "part": "snippet,contentDetails",
                "forUsername": username,
                "key": api_key,
            },
            referer="https://www.youtube.com/",
            headers={"Accept": "application/json"},
        )
        return self._youtube_channel_result(payload)

    def _youtube_channel_result(self, payload, fallback_url=None):
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not items:
            return None
        item = items[0]
        channel_id = item.get("id")
        snippet = item.get("snippet", {})
        content_details = item.get("contentDetails", {})
        uploads_playlist = (
            content_details
            .get("relatedPlaylists", {})
            .get("uploads")
        )
        custom_url = str(snippet.get("customUrl") or "").strip()
        if custom_url and not custom_url.startswith("@"):
            custom_url = custom_url.strip("/")
        if custom_url.startswith("@"):
            url = f"https://www.youtube.com/{custom_url}"
        else:
            url = fallback_url or f"https://www.youtube.com/channel/{channel_id}"
        return {
            "source_id": channel_id,
            "display_name": snippet.get("title") or channel_id,
            "url": url,
            "uploads_playlist_id": uploads_playlist,
        }

    def _fetch_youtube_api_videos(self, creator, api_key):
        channel_id = creator.get("source_id")
        uploads_playlist = creator.get("uploads_playlist_id")
        if not uploads_playlist and channel_id:
            resolved = self._fetch_youtube_channel_by_id(channel_id, api_key)
            if resolved:
                creator.update({key: value for key, value in resolved.items() if value})
                uploads_playlist = resolved.get("uploads_playlist_id")
        if not uploads_playlist:
            raise RuntimeError("YouTube uploads playlist is unavailable for this channel.")

        payload = self._get_json(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params={
                "part": "snippet,contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": min(50, max(1, int(self.data.get("settings", {}).get("max_results", 24)))),
                "key": api_key,
            },
            referer=creator.get("url") or "https://www.youtube.com/",
            headers={"Accept": "application/json"},
        )
        videos = []
        for item in payload.get("items", []) if isinstance(payload, dict) else []:
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})
            resource = snippet.get("resourceId", {})
            video_id = content_details.get("videoId") or resource.get("videoId")
            title = snippet.get("title") or ""
            if not video_id or title.lower() in {"deleted video", "private video"}:
                continue
            thumbnails = snippet.get("thumbnails", {}) or {}
            thumbnail = (
                thumbnails.get("maxres", {}).get("url")
                or thumbnails.get("high", {}).get("url")
                or thumbnails.get("medium", {}).get("url")
                or thumbnails.get("default", {}).get("url")
            )
            videos.append(self._video_item(
                platform="youtube",
                creator=creator,
                source_id=video_id,
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                published_at=content_details.get("videoPublishedAt") or snippet.get("publishedAt"),
                thumbnail=thumbnail,
                extra={"description": snippet.get("description") or ""},
            ))
        return videos

    def _fetch_youtube_feed_videos(self, creator, channel_id):
        xml_text = self._get_text(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
        root = ElementTree.fromstring(xml_text)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "media": "http://search.yahoo.com/mrss/",
            "yt": "http://www.youtube.com/xml/schemas/2015",
        }
        videos = []
        for entry in root.findall("atom:entry", ns):
            title = self._xml_text(entry.find("atom:title", ns))
            link = entry.find("atom:link", ns)
            video_id = self._xml_text(entry.find("yt:videoId", ns))
            published = self._xml_text(entry.find("atom:published", ns))
            media_group = entry.find("media:group", ns)
            thumbnail = None
            if media_group is not None:
                thumb = media_group.find("media:thumbnail", ns)
                thumbnail = thumb.attrib.get("url") if thumb is not None else None
            videos.append(self._video_item(
                platform="youtube",
                creator=creator,
                source_id=video_id or (link.attrib.get("href") if link is not None else title),
                title=title,
                url=link.attrib.get("href") if link is not None else f"https://www.youtube.com/channel/{channel_id}",
                published_at=published,
                thumbnail=thumbnail,
            ))
        return videos

    def _fetch_video_discussion_context(self, video):
        platform = video.get("platform")
        context = {
            "description": "",
            "comments": [],
            "notes": [],
        }
        try:
            if platform == "bilibili":
                return self._fetch_bilibili_video_context(video)
            if platform == "youtube":
                return self._fetch_youtube_video_context(video)
            context["notes"].append("Unsupported video platform.")
        except BilibiliRateLimitError as exc:
            context["notes"].append(str(exc))
        except Exception as exc:
            context["notes"].append(f"Video context fetch failed: {str(exc)[:200]}")
        return context

    def _fetch_bilibili_video_context(self, video):
        bvid = str(video.get("source_id") or "").strip()
        params = {"bvid": bvid} if bvid.upper().startswith("BV") else {"aid": bvid}
        detail = self._get_json(
            "https://api.bilibili.com/x/web-interface/view",
            params=params,
            referer=video.get("url") or "https://www.bilibili.com/",
            headers={"Accept": "application/json, text/plain, */*"},
        )
        data = detail.get("data", {}) if isinstance(detail, dict) else {}
        aid = data.get("aid") or (bvid if bvid.isdigit() else None)
        description = data.get("desc") or ""
        comments = []
        if aid:
            comments = self._fetch_bilibili_hot_comments(aid, video)
        return {
            "description": self._clean_context_text(description, 1600),
            "comments": comments,
            "notes": [] if comments else ["No Bilibili hot comments were available."],
        }

    def _fetch_bilibili_hot_comments(self, aid, video):
        endpoints = [
            (
                "https://api.bilibili.com/x/v2/reply/main",
                {"type": 1, "oid": aid, "mode": 3, "ps": 5, "next": 0},
            ),
            (
                "https://api.bilibili.com/x/v2/reply",
                {"type": 1, "oid": aid, "sort": 2, "ps": 5},
            ),
        ]
        for url, params in endpoints:
            payload = self._get_json(
                url,
                params=params,
                referer=video.get("url") or "https://www.bilibili.com/",
                headers={"Accept": "application/json, text/plain, */*"},
            )
            if payload.get("code") != 0:
                continue
            replies = payload.get("data", {}).get("replies", []) or []
            comments = []
            for item in replies[:5]:
                content = item.get("content", {}) if isinstance(item, dict) else {}
                member = item.get("member", {}) if isinstance(item, dict) else {}
                text = self._clean_context_text(content.get("message") or "", 500)
                if not text:
                    continue
                comments.append({
                    "author": self._clean_context_text(member.get("uname") or "Bilibili user", 80),
                    "text": text,
                    "like_count": item.get("like"),
                })
            if comments:
                return comments[:5]
        return []

    def _fetch_youtube_video_context(self, video):
        video_id = str(video.get("source_id") or "").strip()
        api_key, _ = self._youtube_api_key_config()
        notes = []
        if api_key:
            try:
                return self._fetch_youtube_api_context(video_id, api_key, video)
            except Exception as exc:
                notes.append(f"YouTube API fetch failed: {str(exc)[:180]}")

        if not api_key:
            notes.append("YouTube API key is not configured, so comments are unavailable.")
        description = ""
        try:
            html_text = self._get_text(
                video.get("url") or f"https://www.youtube.com/watch?v={video_id}",
                referer="https://www.youtube.com/",
            )
            description = self._extract_youtube_description(html_text)
        except Exception as exc:
            notes.append(f"YouTube page description fetch failed: {str(exc)[:160]}")
        return {
            "description": self._clean_context_text(description, 1600),
            "comments": [],
            "notes": notes,
        }

    def _fetch_youtube_api_context(self, video_id, api_key, video):
        description = ""
        notes = []
        detail = self._get_json(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet",
                "id": video_id,
                "key": api_key,
            },
            referer="https://www.youtube.com/",
            headers={"Accept": "application/json"},
        )
        items = detail.get("items", []) if isinstance(detail, dict) else []
        if items:
            description = items[0].get("snippet", {}).get("description") or ""

        comments = []
        try:
            payload = self._get_json(
                "https://www.googleapis.com/youtube/v3/commentThreads",
                params={
                    "part": "snippet",
                    "videoId": video_id,
                    "maxResults": 5,
                    "order": "relevance",
                    "textFormat": "plainText",
                    "key": api_key,
                },
                referer=video.get("url") or "https://www.youtube.com/",
                headers={"Accept": "application/json"},
            )
            for item in payload.get("items", [])[:5]:
                snippet = (
                    item.get("snippet", {})
                    .get("topLevelComment", {})
                    .get("snippet", {})
                )
                text = self._clean_context_text(snippet.get("textDisplay") or "", 500)
                if not text:
                    continue
                comments.append({
                    "author": self._clean_context_text(snippet.get("authorDisplayName") or "YouTube user", 80),
                    "text": text,
                    "like_count": snippet.get("likeCount"),
                })
        except Exception as exc:
            notes.append(f"YouTube comments unavailable: {str(exc)[:180]}")

        if not comments:
            notes.append("No YouTube hot comments were available.")
        return {
            "description": self._clean_context_text(description, 1600),
            "comments": comments,
            "notes": notes,
        }

    def _build_discussion_prompt(self, video, context):
        comments = context.get("comments") or []
        comment_lines = []
        for index, comment in enumerate(comments[:5], start=1):
            likes = comment.get("like_count")
            like_text = f", likes: {likes}" if likes is not None else ""
            comment_lines.append(
                f"{index}. {comment.get('author') or 'User'}{like_text}: {comment.get('text') or ''}"
            )
        if not comment_lines:
            comment_lines.append("No hot comments were available.")

        notes = context.get("notes") or []
        note_text = "\n".join(f"- {note}" for note in notes) if notes else "- None"
        description = context.get("description") or "No description was available."
        return (
            "Doctor给你分享了一个视频。以下内容是系统为 Saya 隐式整理的视频上下文，不要把这段上下文本身复述出来，"
            "也不要说你已经实际观看了视频本体。请主要基于视频简介理解内容，再把评论作为辅助信号；"
            "可以结合你对 Doctor 的长期记忆和当前对话语境，做一点温和的思考，但不要编造没有依据的信息。\n\n"
            "[Video]\n"
            f"Platform: {video.get('platform') or ''}\n"
            f"Creator: {video.get('creator_name') or ''}\n"
            f"Title: {video.get('title') or ''}\n"
            f"URL: {video.get('url') or ''}\n"
            f"Published: {video.get('published_at') or ''}\n\n"
            "[Primary Context: Video Description]\n"
            f"{description}\n\n"
            "[Secondary Context: Top Comments]\n"
            f"{chr(10).join(comment_lines)}\n\n"
            "[Fetch Notes]\n"
            f"{note_text}\n\n"
            "请用中文自然回应 Doctor：先说“Doctor给你分享了一个视频”，然后基于简介为主给出一段简短的基本了解；"
            "再补充评论区可能透露出的观众反应；最后给出 2-3 个适合继续聊下去的方向。"
        ) + (
            "\n\n[Response Style Override]\n"
            "Hard requirement: reply in exactly one natural Chinese paragraph.\n"
            "Saya should mainly share her feelings after reading the video description, not summarize the description or reviews.\n"
            "Use comments only as faint background context; do not separately report or summarize audience reactions.\n"
            "Do not use headings, bullet points, numbered lists, line breaks, or a report/digest style.\n"
            "Do not end with multiple discussion directions. If natural, include at most one gentle follow-up question inside the same paragraph.\n"
        )

    def _resolve_bilibili_creator(self, value):
        mid = self._extract_bilibili_mid(value)
        if mid:
            return {
                "source_id": str(mid),
                "url": f"https://space.bilibili.com/{mid}",
            }

        search_page = f"https://search.bilibili.com/upuser?keyword={quote_plus(value)}"
        self._prime_bilibili_session(search_page)
        try:
            payload = self._get_json(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={
                    "search_type": "bili_user",
                    "keyword": value,
                    "page": 1,
                    "order": "totalrank",
                    "duration": 0,
                    "tids": 0,
                    "jsonp": "jsonp",
                },
                referer=search_page,
                headers={"Accept": "application/json, text/plain, */*"},
            )
            resolved = self._pick_bilibili_user_result(payload.get("data", {}).get("result", []), value)
            if resolved:
                return resolved
        except Exception:
            pass

        return self._resolve_bilibili_from_search_page(value, search_page)

    def _fetch_bilibili_videos(self, creator):
        mid = creator.get("source_id")
        payload = None
        referer = f"https://space.bilibili.com/{mid}/video"
        self._prime_bilibili_session(referer)

        try:
            payload = self._get_json(
                "https://api.bilibili.com/x/space/wbi/arc/search",
                params=self._signed_bilibili_params({
                    "mid": mid,
                    "pn": 1,
                    "ps": 12,
                    "order": "pubdate",
                }),
                referer=referer,
                headers={"Accept": "application/json, text/plain, */*"},
            )
        except BilibiliRateLimitError:
            raise
        except Exception:
            payload = None

        if not payload or payload.get("code") != 0:
            try:
                payload = self._get_json(
                    "https://api.bilibili.com/x/space/arc/search",
                    params={"mid": mid, "pn": 1, "ps": 12, "order": "pubdate", "jsonp": "jsonp"},
                    referer=referer,
                    headers={"Accept": "application/json, text/plain, */*"},
                )
            except BilibiliRateLimitError:
                raise
            except Exception:
                payload = None
        if not payload or payload.get("code") != 0:
            dynamic_videos = self._fetch_bilibili_dynamic_videos(creator)
            if dynamic_videos:
                return dynamic_videos
            raise RuntimeError("Bilibili video list request failed")

        vlist = payload.get("data", {}).get("list", {}).get("vlist", []) or []
        videos = []
        for item in vlist:
            bvid = item.get("bvid") or item.get("aid")
            created = item.get("created")
            published = datetime.fromtimestamp(created, timezone.utc).isoformat() if created else None
            pic = item.get("pic")
            if pic and pic.startswith("//"):
                pic = "https:" + pic
            videos.append(self._video_item(
                platform="bilibili",
                creator=creator,
                source_id=str(bvid),
                title=item.get("title") or "",
                url=f"https://www.bilibili.com/video/{bvid}" if bvid else creator.get("url"),
                published_at=published,
                thumbnail=pic,
                extra={
                    "play_count": item.get("play"),
                    "length": item.get("length"),
                },
            ))
        return videos

    def _fetch_bilibili_dynamic_videos(self, creator):
        mid = creator.get("source_id")
        referer = f"https://space.bilibili.com/{mid}/dynamic"
        payload = self._get_json(
            "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
            params={
                "host_mid": mid,
                "timezone_offset": -480,
                "features": "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,decorationCard,forwardListHidden,ugcDelete,onlyfansQaCard",
            },
            referer=referer,
            headers={"Accept": "application/json, text/plain, */*"},
        )
        if payload.get("code") != 0:
            return []

        videos = []
        for item in payload.get("data", {}).get("items", []) or []:
            modules = item.get("modules", {}) if isinstance(item, dict) else {}
            module_dynamic = modules.get("module_dynamic", {}) or {}
            module_author = modules.get("module_author", {}) or {}
            major = module_dynamic.get("major", {}) or {}
            archive = major.get("archive") or {}
            if not archive:
                continue

            bvid = archive.get("bvid") or archive.get("aid")
            title = archive.get("title") or archive.get("desc") or ""
            jump_url = archive.get("jump_url") or ""
            if jump_url.startswith("//"):
                jump_url = "https:" + jump_url
            elif jump_url.startswith("/"):
                jump_url = "https://www.bilibili.com" + jump_url
            if not jump_url and bvid:
                jump_url = f"https://www.bilibili.com/video/{bvid}"

            cover = archive.get("cover") or archive.get("pic")
            if cover and cover.startswith("//"):
                cover = "https:" + cover

            pub_ts = module_author.get("pub_ts") or archive.get("pub_ts") or archive.get("ctime")
            published = datetime.fromtimestamp(pub_ts, timezone.utc).isoformat() if pub_ts else None
            videos.append(self._video_item(
                platform="bilibili",
                creator=creator,
                source_id=str(bvid or jump_url or title),
                title=title,
                url=jump_url or creator.get("url"),
                published_at=published,
                thumbnail=cover,
                extra={
                    "length": archive.get("duration_text"),
                },
            ))
        return videos

    def _video_item(self, platform, creator, source_id, title, url, published_at=None, thumbnail=None, extra=None):
        item = {
            "id": self._stable_id(platform, str(source_id or title or url)),
            "platform": platform,
            "creator_id": creator.get("id"),
            "creator_name": creator.get("display_name") or creator.get("name"),
            "title": re.sub(r"\s+", " ", str(title or "")).strip(),
            "url": url,
            "published_at": self._normalize_datetime(published_at),
            "thumbnail": thumbnail,
            "source_id": str(source_id or ""),
        }
        if extra:
            item.update(extra)
        score, reasons = self._score_video(item)
        item["score"] = score
        item["reasons"] = reasons
        return item

    def _score_video(self, video):
        score = 55.0
        reasons = ["followed creator"]
        age_days = self._age_days(video.get("published_at"))
        if age_days is not None:
            freshness = max(0.0, 35.0 * math.exp(-age_days / 10.0))
            score += freshness
            if age_days <= 2:
                reasons.append("new update")
            elif age_days <= 14:
                reasons.append("recent")

        title = video.get("title", "")
        lower_title = title.lower()
        quality = 5.0
        if 8 <= len(title) <= 90:
            quality += 4.0
        high_signal_keywords = [
            "analysis", "explained", "review", "tutorial", "deep dive", "course",
            "解析", "深度", "教程", "复盘", "评测", "访谈", "纪录", "总结", "研究",
        ]
        if any(keyword in lower_title or keyword in title for keyword in high_signal_keywords):
            quality += 5.0
            reasons.append("high-signal title")
        if title.count("!") + title.count("?") + title.count("！") + title.count("？") <= 2:
            quality += 2.0
        score += quality

        if video.get("platform") == "bilibili" and video.get("play_count"):
            try:
                score += min(4.0, math.log10(max(1, int(video["play_count"]))))
            except (TypeError, ValueError):
                pass

        return round(score, 2), reasons[:4]

    def _rank_and_dedupe(self, videos):
        seen = set()
        clean = []
        for video in videos:
            key = (video.get("platform"), video.get("source_id") or video.get("url"))
            if key in seen or not video.get("title"):
                continue
            seen.add(key)
            clean.append(video)
        return sorted(clean, key=lambda item: (item.get("score", 0), item.get("published_at") or ""), reverse=True)

    def _cache_expired(self):
        now = datetime.now(timezone.utc)
        bilibili_cookie_configured = self._bilibili_cookie_configured()
        for creator in self.data.get("creators", []):
            if not creator.get("enabled", True):
                continue
            next_refresh_at = self._parse_datetime(creator.get("next_refresh_at"))
            if next_refresh_at and next_refresh_at <= now:
                return True
            if (
                bilibili_cookie_configured
                and creator.get("platform") == "bilibili"
                and creator.get("status") == "limited"
                and not creator.get("rate_limited_with_cookie")
            ):
                return True

        last_checked = self.data.get("cache", {}).get("last_checked")
        if not last_checked:
            return True
        last_dt = self._parse_datetime(last_checked)
        if not last_dt:
            return True
        ttl = self.data.get("settings", {}).get("cache_ttl_minutes", 60)
        age_seconds = (now - last_dt).total_seconds()
        return age_seconds > ttl * 60

    def _prime_bilibili_session(self, referer=None):
        try:
            self._get_text("https://www.bilibili.com/", referer="https://www.bilibili.com/")
            if referer:
                self._get_text(referer, referer="https://www.bilibili.com/")
        except Exception:
            pass

    def _resolve_bilibili_from_search_page(self, value, search_page=None):
        search_page = search_page or f"https://search.bilibili.com/upuser?keyword={quote_plus(value)}"
        text = self._get_text(search_page, referer="https://www.bilibili.com/")
        candidates = []
        patterns = (
            (r'"mid"\s*:\s*"?(\d+)"?.{0,900}?"uname"\s*:\s*"([^"]+)"', 1, 2),
            (r'"uname"\s*:\s*"([^"]+)".{0,900}?"mid"\s*:\s*"?(\d+)"?', 2, 1),
            (r'space\.bilibili\.com/(\d+)[^<]{0,500}(?:title|alt)="([^"]+)"', 1, 2),
        )
        for pattern, mid_group, name_group in patterns:
            for match in re.finditer(pattern, text, flags=re.S):
                mid = match.group(mid_group)
                name = self._decode_jsonish_text(match.group(name_group))
                if mid and name:
                    candidates.append({"mid": mid, "uname": name})
        return self._pick_bilibili_user_result(candidates, value)

    def _pick_bilibili_user_result(self, results, value):
        if not isinstance(results, list):
            return None
        target = self._decode_jsonish_text(value).casefold()
        candidates = []
        for item in results:
            if not isinstance(item, dict):
                continue
            mid = item.get("mid")
            name = self._decode_jsonish_text(item.get("uname") or item.get("name") or "")
            if mid and name:
                candidates.append({"mid": mid, "uname": name})
        if not candidates:
            return None
        exact = next((item for item in candidates if item["uname"].casefold() == target), None)
        compact_target = re.sub(r"\s+", "", target)
        compact = next(
            (item for item in candidates if re.sub(r"\s+", "", item["uname"].casefold()) == compact_target),
            None,
        )
        item = exact or compact or candidates[0]
        return {
            "source_id": str(item["mid"]),
            "display_name": item["uname"] or value,
            "url": f"https://space.bilibili.com/{item['mid']}",
        }

    def _signed_bilibili_params(self, params):
        signed = {}
        for key, value in (params or {}).items():
            cleaned = re.sub(r"[!'()*]", "", str(value))
            signed[str(key)] = cleaned
        signed["wts"] = str(int(datetime.now(timezone.utc).timestamp()))
        query = urlencode(sorted(signed.items()))
        signed["w_rid"] = hashlib.md5((query + self._get_bilibili_mixin_key()).encode("utf-8")).hexdigest()
        return signed

    def _get_bilibili_mixin_key(self):
        now = datetime.now(timezone.utc)
        if (
            self._bilibili_mixin_key
            and self._bilibili_mixin_key_expires_at
            and self._bilibili_mixin_key_expires_at > now
        ):
            return self._bilibili_mixin_key

        payload = self._get_json(
            "https://api.bilibili.com/x/web-interface/nav",
            referer="https://www.bilibili.com/",
            headers={"Accept": "application/json, text/plain, */*"},
        )
        wbi_img = payload.get("data", {}).get("wbi_img", {}) if isinstance(payload, dict) else {}
        img_key = Path(urlparse(wbi_img.get("img_url", "")).path).stem
        sub_key = Path(urlparse(wbi_img.get("sub_url", "")).path).stem
        raw_key = img_key + sub_key
        if len(raw_key) < 64:
            raise RuntimeError("Bilibili WBI key unavailable")

        mixin_key = "".join(raw_key[index] for index in self.BILIBILI_WBI_KEY_TABLE)[:32]
        self._bilibili_mixin_key = mixin_key
        self._bilibili_mixin_key_expires_at = now + timedelta(hours=12)
        return mixin_key

    def _get_text(self, url, params=None, referer=None, headers=None):
        response = self.session.get(
            url,
            params=params,
            headers=self._headers(url=url, referer=referer, extra=headers),
            timeout=12,
        )
        self._raise_for_bilibili_rate_limit(response, url)
        response.raise_for_status()
        return response.text

    def _get_json(self, url, params=None, referer=None, headers=None):
        response = self.session.get(
            url,
            params=params,
            headers=self._headers(url=url, referer=referer, extra=headers),
            timeout=12,
        )
        self._raise_for_bilibili_rate_limit(response, url)
        response.raise_for_status()
        data = response.json()
        if self._is_bilibili_url(url):
            code = data.get("code") if isinstance(data, dict) else None
            if code in {-799, -352, -412}:
                message = data.get("message") or data.get("msg") or "Bilibili rate limited"
                raise BilibiliRateLimitError(f"Bilibili limited requests: {message}")
        return data

    def _extract_youtube_description(self, html_text):
        text = str(html_text or "")
        patterns = [
            r'"shortDescription"\s*:\s*"((?:\\.|[^"\\])*)"',
            r'"description"\s*:\s*\{\s*"simpleText"\s*:\s*"((?:\\.|[^"\\])*)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.S)
            if not match:
                continue
            raw = match.group(1)
            try:
                return json.loads(f'"{raw}"')
            except Exception:
                return self._decode_jsonish_text(raw)
        meta = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', text, flags=re.I | re.S)
        if meta:
            return html.unescape(meta.group(1))
        return ""

    def _clean_context_text(self, value, limit):
        text = html.unescape(str(value or ""))
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        limit = max(80, int(limit or 500))
        if len(text) > limit:
            return text[:limit].rstrip() + "..."
        return text

    def _is_bilibili_url(self, url):
        try:
            return "bilibili.com" in urlparse(str(url)).netloc
        except Exception:
            return False

    def _is_youtube_url(self, url):
        try:
            host = urlparse(str(url)).netloc
            return "youtube.com" in host or "youtu.be" in host
        except Exception:
            return False

    def _raise_for_bilibili_rate_limit(self, response, url):
        if not self._is_bilibili_url(url):
            return
        if response.status_code in {412, 429}:
            raise BilibiliRateLimitError(
                f"Bilibili limited requests with HTTP {response.status_code}; using cached results"
            )

    def _headers(self, url=None, referer=None, extra=None):
        default_referer = None
        if url:
            host = urlparse(str(url)).netloc
            if "bilibili.com" in host:
                default_referer = "https://www.bilibili.com/"
            elif "youtube.com" in host:
                default_referer = "https://www.youtube.com/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
        is_bilibili = self._is_bilibili_url(url)
        is_youtube = self._is_youtube_url(url)
        if referer or default_referer:
            headers["Referer"] = referer or default_referer
        if is_bilibili:
            headers["Origin"] = "https://www.bilibili.com"
            bilibili_cookie, _ = self._bilibili_cookie_config()
            if bilibili_cookie:
                headers["Cookie"] = bilibili_cookie
        elif is_youtube:
            headers["Origin"] = "https://www.youtube.com"
            youtube_cookie, _ = self._youtube_cookie_config()
            if youtube_cookie:
                headers["Cookie"] = youtube_cookie
        if extra:
            headers.update(extra)
        return headers

    def _bilibili_cookie_configured(self):
        cookie, _ = self._bilibili_cookie_config()
        return bool(cookie)

    def _bilibili_cookie_config(self):
        return self._local_secret_config("BILIBILI_COOKIE", BILIBILI_COOKIE_FILE)

    def _youtube_cookie_config(self):
        return self._local_secret_config("YOUTUBE_COOKIE", YOUTUBE_COOKIE_FILE)

    def _youtube_api_key_config(self):
        return self._local_secret_config("YOUTUBE_API_KEY", YOUTUBE_API_KEY_FILE)

    def _local_secret_config(self, env_name, file_path):
        env_value = os.environ.get(env_name, "").strip()
        if env_value:
            return env_value, "environment"
        try:
            secret_file = Path(file_path)
            if secret_file.exists():
                for line in secret_file.read_text(encoding="utf-8").splitlines():
                    candidate = line.strip()
                    if candidate and not candidate.startswith("#"):
                        return candidate, "file"
        except Exception as exc:
            print(f"Error loading {env_name}: {exc}")
        return "", None

    def _normalize_platform(self, platform):
        text = str(platform or "").strip().lower()
        if text in {"yt", "youtube"}:
            return "youtube"
        if text in {"bili", "bilibili", "b站"}:
            return "bilibili"
        return text

    def _youtube_handle_candidates(self, value):
        parsed = urlparse(str(value))
        path = parsed.path if parsed.netloc else str(value)
        raw = path.strip().strip("/")
        if raw.startswith("@"):
            raw = raw[1:]
        if raw.startswith("c/") or raw.startswith("user/"):
            raw = raw.split("/", 1)[1]
        compact = re.sub(r"\s+", "", raw)
        candidates = []
        for candidate in (raw, compact):
            candidate = candidate.strip("@/")
            if candidate and candidate not in candidates and not candidate.startswith("channel/"):
                candidates.append(candidate)
        return candidates

    def _extract_youtube_channel_id(self, text):
        text = str(text or "")
        patterns = [
            r"(UC[a-zA-Z0-9_-]{20,})",
            r'"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]{20,})"',
            r'itemprop="channelId"\s+content="(UC[a-zA-Z0-9_-]{20,})"',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    def _extract_youtube_title(self, html):
        for pattern in (r'"title"\s*:\s*"([^"]{1,120})"', r"<title>(.*?)</title>"):
            match = re.search(pattern, html or "", flags=re.S)
            if match:
                title = re.sub(r"\s+-\s+YouTube\s*$", "", match.group(1)).strip()
                return title.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
        return None

    def _extract_bilibili_mid(self, value):
        text = str(value or "")
        match = re.search(r"space\.bilibili\.com/(\d+)", text)
        if match:
            return match.group(1)
        if re.fullmatch(r"\d{3,}", text.strip()):
            return text.strip()
        return None

    def _decode_jsonish_text(self, value):
        text = html.unescape(str(value or "")).replace("\\/", "/")
        if "\\u" in text or "\\x" in text:
            try:
                text = text.encode("utf-8").decode("unicode_escape")
            except UnicodeDecodeError:
                pass
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def _xml_text(self, element):
        return element.text.strip() if element is not None and element.text else ""

    def _normalize_datetime(self, value):
        dt = self._parse_datetime(value)
        return dt.isoformat() if dt else None

    def _parse_datetime(self, value):
        if not value:
            return None
        text = str(value).strip()
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    def _age_days(self, value):
        dt = self._parse_datetime(value)
        if not dt:
            return None
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)

    def _now_iso(self):
        return datetime.now(timezone.utc).isoformat()

    def _coerce_int(self, value, default, minimum, maximum):
        try:
            return max(minimum, min(maximum, int(value)))
        except (TypeError, ValueError):
            return default

    def _stable_id(self, *parts):
        raw = "::".join(str(part) for part in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


video_recommendation_service = VideoRecommendationService()
