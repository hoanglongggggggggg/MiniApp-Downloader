# py_client

Tải phim từ danh sách đề xuất của các mini-app họ `duolai`.

```bash
pip install PySide6 requests pycryptodome
```

Đặt cookie Douyin vào `cookie.local.txt` (header thô hoặc JSON export), rồi chạy:

```bash
python gui_qt.py
```

Chọn app → chọn phim trong danh sách đề xuất → tích tập → **Tải**.

CLI: `python miniapp_client.py browse <app>` · `playlist <app> <id>` · `download <app> <id> --episodes 1-3`

Thêm app mới: sửa `sources.py`.
