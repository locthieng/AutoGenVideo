# 🎬 AI Shorts Maker

Dán kịch bản → nhận **video YouTube Shorts hoàn chỉnh** (1080×1920) gồm:
- 🗣️ Giọng đọc tiếng Anh tự nhiên (Edge TTS, **miễn phí**)
- 🖼️ Ảnh AI cho từng cảnh + hiệu ứng zoom/pan chuyển động
- 💬 Phụ đề kiểu Shorts: chữ to, hiện 1–3 từ, **tô vàng từ đang đọc**
- 🎵 Nhạc nền (tùy chọn)

## Cách dùng

Nhấp đúp **`run.bat`** → trình duyệt tự mở `http://localhost:8000`. Có 2 chế độ:

### 🙌 Chế độ bán tự động (dùng gói Gemini Pro, không tốn thêm tiền)
1. **Lần đầu:** nhấp đúp **`login_gemini.bat`** → chọn *Sign in with Google* → đăng nhập tài khoản Gemini Pro → gõ `/quit`.
2. Dán kịch bản → bấm **📝 Chuẩn bị cảnh & mô tả ảnh**. Gemini (qua tài khoản của bạn) viết mô tả ảnh cho từng cảnh.
3. Ở mỗi cảnh: bấm **📋 Copy mô tả** → dán vào https://gemini.google.com → tạo ảnh.
4. Đưa ảnh vào cảnh bằng 1 trong 3 cách:
   - **Kéo thả** file ảnh vào ô ảnh của cảnh
   - **Chuột phải ảnh trên Gemini → Sao chép hình ảnh**, rồi bấm vào cảnh và **Ctrl+V** (tự nhảy sang cảnh tiếp theo)
   - Tải hết ảnh về rồi **kéo nhiều ảnh cùng lúc** vào khung 📥 (xếp theo thứ tự tải về)
5. Bấm **🎬 Dựng video**.

> 💡 Tạo tất cả ảnh trong **cùng một cuộc trò chuyện** Gemini để nhân vật giống nhau giữa các cảnh.

### ⚡ Chế độ tự động (API key, tính phí theo ảnh)
Điền key vào file `.env` rồi chạy lại `run.bat`:

| Nhà cung cấp | Lấy key ở đâu | Ghi chú |
|---|---|---|
| **Gemini API** | https://aistudio.google.com/apikey | `IMAGE_PROVIDER=gemini`, điền `GEMINI_API_KEY`. Cần bật thanh toán. Gói Gemini Pro **không** bao gồm API |
| **OpenAI** | https://platform.openai.com/api-keys | `IMAGE_PROVIDER=openai`, điền `OPENAI_API_KEY` |
| **Ảnh test** | không cần | Ảnh nền màu, để thử quy trình miễn phí |

### Thay ảnh & dựng lại
Mở dự án cũ ở mục **Dự án gần đây**, thay ảnh bất kỳ cảnh nào (hoặc đổi giọng, nhạc) rồi bấm **🎬 Dựng video** lại.

> 💰 Chi phí tham khảo: 1 video ~30–45s ≈ 8–12 ảnh. Kiểm tra bảng giá mới nhất của nhà cung cấp.

## Mẹo viết kịch bản

- Viết **tiếng Anh**, câu ngắn. Mỗi câu (hoặc 2 câu ngắn) sẽ thành **1 cảnh = 1 ảnh**.
- **Câu đầu tiên phải gây tò mò** (đó là phần giữ chân người xem).
- **Dưới 60 giây** (~130–150 từ ở tốc độ +10%).
- Ô *"Chỉ đạo thêm cho AI"* dùng để mô tả nhân vật chính → ảnh các cảnh đồng bộ hơn.

## Cấu trúc thư mục output

```
output/20261005-101511-ten-video/
├── ten-video.mp4     ← video hoàn chỉnh
├── scenes.json       ← câu thoại + prompt ảnh từng cảnh
├── images/           ← ảnh AI (dùng lại làm thumbnail)
├── audio/            ← giọng đọc
└── subs.ass          ← phụ đề
```

## Cấu trúc code

| File | Chức năng |
|---|---|
| `app/script_splitter.py` | Chia kịch bản thành các cảnh |
| `app/tts.py` | Giọng đọc + mốc thời gian từng từ |
| `app/images.py` | AI viết prompt + tạo ảnh |
| `app/subtitles.py` | Phụ đề kiểu Shorts |
| `app/render.py` | Ghép video bằng ffmpeg |
| `app/pipeline.py` | Điều phối toàn bộ quy trình |
| `app/server.py` + `app/static/index.html` | Giao diện web |
