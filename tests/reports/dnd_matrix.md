# Ma trận kéo thả

✅ = chiến lược đó một mình đã đặt đúng từ vào chỗ trống.

| Trang thử | playwright | mouse_steps | mouse_hold | touch | html5_events | pointer_events | click_place |
|---|---|---|---|---|---|---|---|
| HTML5 draggable + ondrop | ✅ | ✅ | ✅ | — | ✅ | — | — |
| mousedown/move/up + ngưỡng 5px + ghost | ✅ | ✅ | ✅ | — | — | ✅ | — |
| Pointer events + isTrusted (kiểu dnd-kit) | ✅ | ✅ | ✅ | ✅ | — | — | — |
| jQuery UI draggable/droppable | ✅ | ✅ | ✅ | — | — | ✅ | — |
| SortableJS (HTML5 native) | ✅ | ✅ | ✅ | ✅ | — | — | — |
| SortableJS forceFallback (chuột) | — | ✅ | ✅ | ✅ | — | ✅ | — |
| interact.js dropzone | ✅ | ✅ | ✅ | — | — | ✅ | — |
| Chỉ touch events (kiểu mobile) | — | — | — | ✅ | — | — | — |
| Bấm từ rồi bấm chỗ trống | — | — | — | — | — | — | ✅ |
| Chọn rồi thả, ô trống là <ul> riêng (British Council) | — | — | — | — | — | — | ✅ |
| Nhấn giữ 400ms mới kéo | — | — | ✅ | — | — | — | — |
| HTML5 trong iframe khác origin | ✅ | ✅ | ✅ | — | ✅ | — | — |
