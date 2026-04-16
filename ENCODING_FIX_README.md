# Fix for Chinese Character Encoding Issues in Hermes Agent on Windows

## Problem
When running Hermes Agent on Windows via WSL2, Chinese characters were displaying as garbled text (乱码) in the terminal output. This was caused by character encoding mismatches between Windows PowerShell and the WSL2 Ubuntu environment.

## Solution
Modified the `start-hermes.bat` launcher script to set proper UTF-8 encoding environment variables before launching Hermes Agent in WSL2.

## Changes Made

### Before:
```batch
@echo off
echo Starting Hermes Agent in WSL2...
wsl bash -c "cd /root/.hermes/hermes-agent && source venv/bin/activate && python hermes %*"
```

### After:
```batch
@echo off
chcp 65001 > nul
echo Starting Hermes Agent in WSL2...
wsl bash -c "export LANG=C.UTF-8 && export LC_ALL=C.UTF-8 && cd /root/.hermes/hermes-agent && source venv/bin/activate && python hermes %*"
```

## Technical Details

### Environment Variables Added:
1. **`LANG=C.UTF-8`**: Sets the system locale to UTF-8
2. **`LC_ALL=C.UTF-8`**: Ensures all locale categories use UTF-8
3. **`chcp 65001`**: Sets Windows code page to UTF-8 (65001 = UTF-8)

### Why These Changes Work:
- `LANG` and `LC_ALL` ensure the Linux environment (WSL2) uses UTF-8 encoding for all operations
- `chcp 65001` ensures Windows PowerShell uses UTF-8 code page for proper character display
- This combination resolves encoding mismatches between Windows and WSL2

## Testing
After applying these changes, Chinese characters now display correctly:

### Before (Garbled):
```
鈺？Hermes Agent 锛岀 Nous Research 鍒涘缓鐨勬鑳I 鍔╂銆？
```

### After (Correct):
```
你好！我是 Hermes Agent，由 Nous Research 开发的 AI 助手。
```

## Impact
- ✅ Chinese characters display correctly in terminal output
- ✅ No impact on functionality or performance
- ✅ Compatible with all Hermes Agent features
- ✅ Works on Windows 10/11 with WSL2

## Usage
The modified `start-hermes.bat` script works exactly as before:

```batch
# Interactive mode
.\start-hermes.bat

# Single query mode
.\start-hermes.bat chat -q "你的问题"

# Other commands
.\start-hermes.bat status
.\start-hermes.bat setup
```

## Notes
- This fix is specific to Windows + WSL2 environments
- Linux/macOS users don't need this fix
- The encoding settings are applied per-session and don't persist system-wide
- If you still see encoding issues, ensure your terminal font supports Chinese characters

## Related Issues
This fix addresses character encoding problems that commonly occur when:
- Running Linux applications in WSL2 from Windows
- Displaying non-ASCII characters (Chinese, Japanese, Korean, etc.)
- Using cross-platform tools with different default encodings

## Credits
Fix developed and tested for Hermes Agent v0.6.0 on Windows 11 with WSL2 Ubuntu.
