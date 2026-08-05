/*
 * Arduino Leonardo / Pro Micro Keyboard HID
 *
 * 透過序列埠接收按鍵指令，模擬真實鍵盤輸入。
 *
 * === 協定 (與 ui.py 的 send_key_arduino 相容) ===
 *   單一字元   a-z / 0-9      -> 按下並釋放該鍵 (tap)
 *   F1 - F12                  -> 功能鍵
 *   N0 - N9                   -> 小鍵盤數字 (numpad)
 *   ENTER / SPACE / TAB / ESC / BACKSPACE
 *   方向鍵     LEFT RIGHT UP DOWN        <-- 本版新增
 *   導覽鍵     HOME END PAGEUP PAGEDOWN INSERT DELETE   <-- 本版新增
 *   修飾鍵     SHIFT CTRL ALT            <-- 本版新增
 *
 * === 進階 (按住 / 放開，供日後 hold/release 用，可選) ===
 *   DOWN:<token>   -> 按住不放 (例如 DOWN:RIGHT、DOWN:a)
 *   UP:<token>     -> 釋放      (例如 UP:RIGHT)
 *
 * 名稱類指令不分大小寫；單一字元維持原樣。每個指令成功回 "OK"，否則 "ERR"。
 *
 * === 實體按鈕（緊急停止）===
 *   紅色按鈕接在 BTN_PIN 與 GND 之間（用內建上拉，不需外接電阻）。
 *   按下時板子主動送出一行 "BTN:STOP"，PC 端收到就強制停止巡邏。
 *   這是【單向上報】，不是指令的回應——PC 端解析序列埠時要能區分兩者。
 *
 * 上傳此代碼到 Arduino Leonardo / Pro Micro。
 */

#include <Keyboard.h>

// ===== 實體按鈕 =====
const int BTN_PIN = 2;            // 按鈕腳位（另一端接 GND）
const unsigned long BTN_DEBOUNCE_MS = 50;   // 消抖：機械接點彈跳約幾十 ms
const unsigned long BTN_REPEAT_MS = 500;    // 同一次按住不重複上報的最小間隔

int btnLast = HIGH;               // 上一次讀到的電位（PULLUP：放開=HIGH、按下=LOW）
unsigned long btnLastChange = 0;  // 電位變化的時間（消抖用）
unsigned long btnLastSent = 0;    // 最後一次上報的時間（防連發）

void setup() {
  Serial.begin(115200);
  Keyboard.begin();
  pinMode(BTN_PIN, INPUT_PULLUP);
}

// 檢查實體按鈕：只在「放開→按下」的邊緣上報一次。
// 【為什麼要自己消抖】機械按鈕放開/按下的瞬間電位會彈跳好幾次，直接用電位判斷會
// 在一次按壓中送出好幾筆 BTN:STOP。停止巡邏本身是冪等的（重複停無害），但連發會
// 塞滿序列埠、拖慢正在送的按鍵指令。
void pollButton() {
  int v = digitalRead(BTN_PIN);
  unsigned long now = millis();
  if (v != btnLast) {             // 電位變了 → 重新計時，等它穩定
    btnLast = v;
    btnLastChange = now;
    return;
  }
  if (v != LOW) return;                             // 沒按著
  if (now - btnLastChange < BTN_DEBOUNCE_MS) return; // 還沒穩定
  if (now - btnLastSent < BTN_REPEAT_MS) return;     // 按住不放 → 不重複上報
  btnLastSent = now;
  Serial.println("BTN:STOP");
}

// 將指令字串對應到 HID keycode。回傳 0 表示不是已知的「具名鍵」。
int keyFromToken(String tok) {
  String u = tok;
  u.toUpperCase();

  // 功能鍵 F1-F12
  if (u.length() >= 2 && u.length() <= 3 && u.charAt(0) == 'F') {
    int n = u.substring(1).toInt();
    if (n >= 1 && n <= 12) return KEY_F1 + (n - 1);
  }

  // 小鍵盤數字 N0-N9 (Numpad)
  if (u.length() == 2 && u.charAt(0) == 'N' && u.charAt(1) >= '0' && u.charAt(1) <= '9') {
    char d = u.charAt(1);
    if (d == '0') return 234;            // KEY_KP_0
    return 225 + (d - '1');              // KEY_KP_1 .. KEY_KP_9
  }

  // 特殊鍵
  if (u == "ENTER" || u == "RETURN") return KEY_RETURN;
  if (u == "SPACE")     return ' ';
  if (u == "TAB")       return KEY_TAB;
  if (u == "ESC")       return KEY_ESC;
  if (u == "BACKSPACE") return KEY_BACKSPACE;

  // 方向鍵
  if (u == "LEFT")  return KEY_LEFT_ARROW;
  if (u == "RIGHT") return KEY_RIGHT_ARROW;
  if (u == "UP")    return KEY_UP_ARROW;
  if (u == "DOWN")  return KEY_DOWN_ARROW;

  // 導覽鍵
  if (u == "HOME")     return KEY_HOME;
  if (u == "END")      return KEY_END;
  if (u == "PAGEUP")   return KEY_PAGE_UP;
  if (u == "PAGEDOWN") return KEY_PAGE_DOWN;
  if (u == "INSERT")   return KEY_INSERT;
  if (u == "DELETE")   return KEY_DELETE;

  // 修飾鍵
  if (u == "SHIFT") return KEY_LEFT_SHIFT;
  if (u == "CTRL")  return KEY_LEFT_CTRL;
  if (u == "ALT")   return KEY_LEFT_ALT;

  return 0;
}

// 解析一個 token 成 keycode；單一字元 (a-z/0-9) 直接用其字元碼。
int resolveKey(String tok) {
  int code = keyFromToken(tok);
  if (code == 0 && tok.length() == 1) {
    code = tok.charAt(0);   // 普通單字元按鍵
  }
  return code;
}

void loop() {
  pollButton();       // 必須在下面的 early return 之前：沒有序列埠指令時也要能按

  if (Serial.available() <= 0) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.length() == 0) return;

  // 進階：按住 / 放開
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

  // 一般：按下 + 釋放 (tap)。delay(20) 確保遊戲偵測得到。
  int code = resolveKey(cmd);
  if (code == 0) { Serial.println("ERR"); return; }

  Keyboard.press(code);
  delay(20);
  Keyboard.release(code);
  Serial.println("OK");
}
