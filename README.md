# AI Shorts Maker

Dán kịch bản tiếng Anh → video Shorts 1080×1920, 30 FPS, gồm ảnh AI có zoom/pan, giọng đọc Edge TTS, phụ đề highlight từng từ và nhạc nền tùy chọn.

## Hai chế độ tự động

- **Gemini web**: dùng tài khoản Gemini trong browser profile riêng.
- **ChatGPT web**: dùng tài khoản ChatGPT trong browser profile riêng.

Cả hai chạy cùng luồng: chia cảnh → nhờ AI viết prompt → tạo ảnh lần lượt trong cùng conversation → tải ảnh → tạo giọng đọc/phụ đề → FFmpeg dựng MP4. UI và endpoint tạo job chỉ nhận hai chế độ này. Project manual/API cũ vẫn mở được để thay ảnh và dựng lại.

## Cách chạy trên Windows

1. Cài Python 3.10+ và Edge hoặc Chrome.
2. Chạy `setup_web.bat` để cài/cập nhật dependencies. File `setup_gemini_web.bat` cũ vẫn dùng được.
3. Chạy `run.bat`, mở giao diện tại `http://localhost:8000`.
4. Chọn Gemini web hoặc ChatGPT web, bấm **Mở … để đăng nhập**. Đăng nhập trong cửa sổ riêng của tool; không dùng session từ browser cá nhân hay Gemini CLI.
5. Nhập kịch bản, chọn giọng đọc/phong cách/nhạc, bấm **Tạo video**.
6. Giữ server và browser mở trong khi chạy. Khi xong, tải MP4 trên giao diện.

Không cần API key. Quyền tạo ảnh và quota phụ thuộc tài khoản/dịch vụ. Không có fallback sang API trả phí. Đăng nhập hoặc xác minh cần thao tác trực tiếp trong browser.

## Tiếp tục và dựng lại

Khi lỗi/quota/timeout, project, conversation và các ảnh đã lưu được giữ lại. Mở **Dự án gần đây**, xử lý yêu cầu trong browser, rồi bấm **Tiếp tục Gemini web** hoặc **Tiếp tục ChatGPT web**. Tool chỉ tạo các ảnh thiếu, rồi dựng video khi đủ ảnh.

Có thể thay ảnh bằng upload, kéo thả hoặc Ctrl+V; chỉnh prompt các cảnh; thêm thumbnail và dựng lại. Thumbnail mặc định hiện 0.5 giây đầu video. Render lại tạo lại TTS với tùy chọn hiện tại.

Mỗi dịch vụ chỉ nhận một job tự động cùng lúc. Gemini và ChatGPT dùng profile/cổng riêng. Sau khi restart server, lịch sử job trong RAM bị mất nhưng project và session browser vẫn còn để tiếp tục.

## Cấu hình `.env`

`run.bat` tạo `.env` từ `.env.example` nếu chưa có. Restart server sau khi đổi cấu hình.

| Thiết lập | Gemini | ChatGPT |
|---|---|---|
| Cổng CDP | `GEMINI_WEB_PORT=9223` | `CHATGPT_WEB_PORT=9224` |
| Timeout mỗi lượt (giây) | `GEMINI_WEB_TIMEOUT=240` | `CHATGPT_WEB_TIMEOUT=360` |
| Chờ đăng nhập (giây) | `GEMINI_WEB_LOGIN_TIMEOUT=300` | `CHATGPT_WEB_LOGIN_TIMEOUT=300` |
| Browser executable tùy chọn | `GEMINI_WEB_BROWSER` | `CHATGPT_WEB_BROWSER` |
| Composer selector | `GEMINI_WEB_COMPOSER_SELECTOR` | `CHATGPT_WEB_COMPOSER_SELECTOR` |
| Response selector | `GEMINI_WEB_RESPONSE_SELECTOR` | `CHATGPT_WEB_RESPONSE_SELECTOR` |
| Download selector | `GEMINI_WEB_DOWNLOAD_SELECTOR` | `CHATGPT_WEB_DOWNLOAD_SELECTOR` |

Hai cổng phải khác nhau. Profiles `.browser-profile/` và `.chatgpt-browser-profile/` được Git ignore.

UI dịch vụ có thể thay đổi. Download selector được tìm trong lượt trả lời mới, nên cần trỏ tới nút tải ảnh thuộc lượt đó. ChatGPT mặc định thử nút tải trên ảnh, sau đó nút Save trong image viewer nếu cần. Tool báo lỗi khi không tải được ảnh; không dùng screenshot thay ảnh gốc. Ảnh tải về được kiểm tra, crop/resize và lưu JPEG 1080×1920 để render.

Chức năng Save ảnh của ChatGPT được mô tả tại [OpenAI Help Center](https://help.openai.com/en/articles/11084440-chatgpt-images-faq). Selectors của adapter cần kiểm tra với UI/tài khoản thực tế; tài liệu này không cam kết tính ổn định của browser automation.

`PORT=8000` cấu hình cổng ứng dụng; `THUMB_SECONDS=0.5` cấu hình thời lượng thumbnail. Nhạc nền đặt trong `assets/music/`.

## Output

```
output/<timestamp>-<title>-<id>/
├── project.json      # options, cảnh, prompt, conversation, đường dẫn video
├── script.txt
├── images/
├── audio/
├── fonts/
├── subs.ass
├── ffmpeg_cmd.txt
└── <title>.mp4
```

## Code

- `app/server.py`: FastAPI endpoints.
- `app/static/index.html`: UI HTML/CSS/JavaScript.
- `app/pipeline.py`: điều phối job, project, resume, render.
- `app/gemini_web.py`: browser/CDP, luồng chat/download dùng chung và Gemini adapter.
- `app/chatgpt_web.py`: ChatGPT adapter, selectors, profile và lock riêng.
- `app/script_splitter.py`: chia cảnh khoảng 7–20 từ, ưu tiên ranh giới câu.
- `app/tts.py`: Edge TTS và WordBoundary.
- `app/subtitles.py`: phụ đề ASS.
- `app/render.py`: FFmpeg, Ken Burns, audio, nhạc, thumbnail.
- `app/images.py`, `app/gemini_cli.py`: helper ảnh/prompt và code provider cũ để tương thích dữ liệu cũ; không có chế độ tạo mới manual/API trong UI.

## Kiểm tra

Chạy `.venv\Scripts\python.exe -m unittest discover -s tests -v`.

Unit tests kiểm tra resume, prompt sai định dạng, download, lock, URL và API. Browser smoke test chạy Edge/Chrome headless với API fixture local để kiểm tra UI; không gọi AI. Test tự động không thay thế kiểm tra tạo ảnh bằng tài khoản thật.
