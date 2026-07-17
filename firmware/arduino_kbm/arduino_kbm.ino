/*
 * Arduino Leonardo / Pro Micro  —  鍵盤 + 滑鼠 HID
 *
 * 本韌體 = 原本 arduino_keyboard.ino(鍵盤部分「一字不改」) + 新增滑鼠指令。
 * 鍵盤協定與行為完全相同，既有功能不受影響。
 *
 * === 鍵盤協定(與原版相同) ===
 *   單一字元 a-z / 0-9、F1-F12、N0-N9(numpad)、
 *   ENTER SPACE TAB ESC BACKSPACE、方向鍵、導覽鍵、修飾鍵；不分大小寫。
 *   DOWN:<token> 按住 / UP:<token> 放開。成功回 "OK"。
 *
 * === 新增滑鼠指令(硬體 HID，反作弊擋不了) ===
 *   WHEEL:<n>        滾輪 n 格(正=上/前，負=下)   <-- KMBox 無滾輪，改用這個
 *   MMOVE:<dx>,<dy>  相對移動
 *   MDOWN:<L|R|M> / MUP:<L|R|M> / MCLICK:<L|R|M>   滑鼠鍵
 *
 * 上傳到 Arduino Leonardo / Pro Micro。
 */

#include <Keyboard.h>
#include <Mouse.h>

void setup() {
  Serial.begin(115200);
  Keyboard.begin();
  Mouse.begin();
}

// ===== 以下 keyFromToken / resolveKey 與原版完全相同 =====
int keyFromToken(String tok) {
  String u = tok;
  u.toUpperCase();

  if (u.length() >= 2 && u.length() <= 3 && u.charAt(0) == 'F') {
    int n = u.substring(1).toInt();
    if (n >= 1 && n <= 12) return KEY_F1 + (n - 1);
  }

  if (u.length() == 2 && u.charAt(0) == 'N' && u.charAt(1) >= '0' && u.charAt(1) <= '9') {
    char d = u.charAt(1);
    if (d == '0') return 234;            // KEY_KP_0
    return 225 + (d - '1');              // KEY_KP_1 .. KEY_KP_9
  }

  if (u == "ENTER" || u == "RETURN") return KEY_RETURN;
  if (u == "SPACE")     return ' ';
  if (u == "TAB")       return KEY_TAB;
  if (u == "ESC")       return KEY_ESC;
  if (u == "BACKSPACE") return KEY_BACKSPACE;

  if (u == "LEFT")  return KEY_LEFT_ARROW;
  if (u == "RIGHT") return KEY_RIGHT_ARROW;
  if (u == "UP")    return KEY_UP_ARROW;
  if (u == "DOWN")  return KEY_DOWN_ARROW;

  if (u == "HOME")     return KEY_HOME;
  if (u == "END")      return KEY_END;
  if (u == "PAGEUP")   return KEY_PAGE_UP;
  if (u == "PAGEDOWN") return KEY_PAGE_DOWN;
  if (u == "INSERT")   return KEY_INSERT;
  if (u == "DELETE")   return KEY_DELETE;

  if (u == "SHIFT") return KEY_LEFT_SHIFT;
  if (u == "CTRL")  return KEY_LEFT_CTRL;
  if (u == "ALT")   return KEY_LEFT_ALT;

  return 0;
}

int resolveKey(String tok) {
  int code = keyFromToken(tok);
  if (code == 0 && tok.length() == 1) {
    code = tok.charAt(0);
  }
  return code;
}

// ===== 新增：滑鼠鍵 token =====
char mouseBtn(String s) {
  s.toUpperCase();
  if (s == "L" || s == "LEFT")   return MOUSE_LEFT;
  if (s == "R" || s == "RIGHT")  return MOUSE_RIGHT;
  if (s == "M" || s == "MIDDLE") return MOUSE_MIDDLE;
  return 0;
}

void loop() {
  if (Serial.available() <= 0) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.length() == 0) return;

  // ===== 新增：滑鼠指令(先處理，前綴不與鍵盤衝突) =====
  if (cmd.startsWith("WHEEL:")) {
    int n = cmd.substring(6).toInt();
    while (n > 0)  { int s = n > 127 ? 127 : n;  Mouse.move(0, 0, s); n -= s; }
    while (n < 0)  { int s = n < -127 ? -127 : n; Mouse.move(0, 0, s); n -= s; }
    Serial.println("OK"); return;
  }
  if (cmd.startsWith("MMOVE:")) {
    String r = cmd.substring(6); int c = r.indexOf(',');
    if (c < 0) { Serial.println("ERR"); return; }
    int dx = r.substring(0, c).toInt(), dy = r.substring(c + 1).toInt();
    while (dx != 0 || dy != 0) {                 // 每次 -127..127
      int sx = dx > 127 ? 127 : (dx < -127 ? -127 : dx);
      int sy = dy > 127 ? 127 : (dy < -127 ? -127 : dy);
      Mouse.move(sx, sy, 0); dx -= sx; dy -= sy;
    }
    Serial.println("OK"); return;
  }
  if (cmd.startsWith("MDOWN:"))  { char b = mouseBtn(cmd.substring(6)); if (b) { Mouse.press(b);   Serial.println("OK"); } else Serial.println("ERR"); return; }
  if (cmd.startsWith("MUP:"))    { char b = mouseBtn(cmd.substring(4)); if (b) { Mouse.release(b); Serial.println("OK"); } else Serial.println("ERR"); return; }
  if (cmd.startsWith("MCLICK:")) { char b = mouseBtn(cmd.substring(7)); if (b) { Mouse.click(b);   Serial.println("OK"); } else Serial.println("ERR"); return; }

  // ===== 以下鍵盤部分與原版完全相同 =====
  if (cmd.startsWith("DOWN:") || cmd.startsWith("UP:")) {
    bool isDown = cmd.startsWith("DOWN:");
    String tok = cmd.substring(cmd.indexOf(':') + 1);
    tok.trim();
    int code = resolveKey(tok);
    if (code == 0) { Serial.println("ERR"); return; }
    if (isDown) Keyboard.press(code);
    else        Keyboard.release(code);
    Serial.println("OK");
    return;
  }

  int code = resolveKey(cmd);
  if (code == 0) { Serial.println("ERR"); return; }

  Keyboard.press(code);
  delay(20);
  Keyboard.release(code);
  Serial.println("OK");
}
