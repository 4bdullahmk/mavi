import pathlib
from collections import OrderedDict
from typing import Dict, Tuple, Any

class CachedProjectReader:
    """
    Cached project reader for local Mavi coding agents.
    
    Security: Path validation is performed by caller safe_path BEFORE calling reader.
    """
    
    def __init__(self, max_entries: int = 128, max_bytes: int = 4000000):
        self.cache: OrderedDict[str, Tuple[Dict[str, Any], str]] = OrderedDict()
        self.total_bytes: int = 0
        self.max_entries: int = max_entries
        self.max_bytes: int = max_bytes
    
    def read(self, path: pathlib.Path, max_file: int = 120000) -> str:
        resolved_path = str(path.resolve())
        try:
            stat_before = path.stat()
        except (FileNotFoundError, OSError):
            raise ValueError('File missing or larger than 120 KB.')
        
        if not path.is_file() or stat_before.st_size > max_file:
            raise ValueError('File missing or larger than 120 KB.')
        
        key = resolved_path
        metadata = {
            'st_dev': stat_before.st_dev,
            'st_ino': stat_before.st_ino,
            'st_size': stat_before.st_size,
            'st_mtime_ns': stat_before.st_mtime_ns,
            'st_ctime_ns': stat_before.st_ctime_ns
        }
        
        if key in self.cache:
            cached_metadata, content = self.cache[key]
            if cached_metadata == metadata:
                # Promote to end (most recently used)
                self.cache.move_to_end(key)
                return content
        
        # Read file
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read(max_file + 1)
            if len(content.encode('utf-8')) > max_file:
                raise ValueError('File missing or larger than 120 KB.')
        except (UnicodeDecodeError, OSError):
            return 'Binary or non-UTF-8 file.'
        
        # Verify metadata after read
        try:
            stat_after = path.stat()
        except (FileNotFoundError, OSError):
            raise ValueError('File missing or larger than 120 KB.')
        
        if not path.is_file() or stat_after.st_size > max_file:
            raise ValueError('File missing or larger than 120 KB.')
        
        # Check if metadata matches
        after_metadata = {
            'st_dev': stat_after.st_dev,
            'st_ino': stat_after.st_ino,
            'st_size': stat_after.st_size,
            'st_mtime_ns': stat_after.st_mtime_ns,
            'st_ctime_ns': stat_after.st_ctime_ns
        }
        
        if after_metadata != metadata:
            raise ValueError('File changed during read.')
        
        # Cache new content
        content_size = len(content.encode('utf-8'))
        
        if key in self.cache:
            old_content_size = len(self.cache[key][1].encode('utf-8'))
            self.total_bytes -= old_content_size
            del self.cache[key]
        if self.max_entries <= 0 or content_size > self.max_bytes:
            return content
        while self.cache and (len(self.cache) >= self.max_entries or
                              self.total_bytes + content_size > self.max_bytes):
            _, (_, old_content) = self.cache.popitem(last=False)
            self.total_bytes -= len(old_content.encode('utf-8'))

        self.cache[key] = (metadata, content)
        self.total_bytes += content_size
        return content
    
    def clear(self):
        """Clear the cache."""
        self.cache.clear()
        self.total_bytes = 0
