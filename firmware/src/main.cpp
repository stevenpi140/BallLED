// 點陣比分看板韌體：從伺服器抓 RGB565 畫面，顯示在 HUB75 LED 面板上。
// 中文字型與版面都在伺服器處理，這裡只負責連網、抓圖、畫圖、換頁。

#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiManager.h>

#include "chime.h"
#include "config.h"

static MatrixPanel_I2S_DMA *display = nullptr;
static Preferences prefs;
static char serverUrl[128] = DEFAULT_SERVER_URL;
static uint16_t frame[FRAME_W * FRAME_H];

static int page = 0;   // 下一次要抓的頁
static int shown = 0;  // 目前顯示中的頁
static int pageCount = 1;
static uint32_t holdUntil = 0;
static uint32_t refreshMs = 0;    // 伺服器要求的重抓間隔：這頁有進行中的比賽時，停留期間定期更新比分
static uint32_t nextRefresh = 0;
static bool paused = false;
static int fails = 0;
static uint8_t brightness = 128;

// 整點跑馬燈：伺服器在畫面回應標頭告訴看板「還有幾毫秒要跑」，時間到再下載長圖、在看板上自己捲動
static String marqueePendingId;  // 等待中的跑馬燈編號
static String marqueeLastId;     // 已經跑過的編號（整點後 30 秒內伺服器會重複告知，靠這個避免重跑）
static uint32_t marqueeDueAt = 0;
static bool marqueeDue = false;

// ---------- 顯示 ----------

static void setupDisplay() {
  HUB75_I2S_CFG cfg(PANEL_RES_X, PANEL_RES_Y, PANEL_CHAIN);
#if defined(BOARD_MATRIXPORTAL_S3)
  // R1, G1, B1, R2, G2, B2, A, B, C, D, E, LAT, OE, CLK
  cfg.gpio = {42, 41, 40, 38, 39, 37, 45, 36, 48, 35, 21, 47, 14, 2};
#elif defined(BOARD_WAVESHARE_S3)
  // 微雪 ESP32-S3-RGB-Matrix（腳位依微雪文件；E 腳是 GPIO 9）
  // R1, G1, B1, R2, G2, B2, A, B, C, D, E, LAT, OE, CLK
  cfg.gpio = {4, 5, 6, 7, 15, 16, 18, 8, 3, 42, 9, 40, 2, 41};
#else
  // ESP32 Trinity／一般 ESP32：函式庫預設腳位，64x64 面板的 E 腳接 GPIO 18（Trinity 官方範例同設定）
  cfg.gpio.e = 18;
#endif
#ifdef PANEL_DRIVER_FM6126A
  cfg.driver = HUB75_I2S_CFG::FM6126A;
#endif
  cfg.clkphase = PANEL_CLKPHASE;
  cfg.double_buff = true;  // 先畫在背景緩衝區再切換，換頁不會閃爍
  display = new MatrixPanel_I2S_DMA(cfg);
  if (!display->begin()) {
    // 一般 ESP32 只能用內部 RAM（約 200KB），128x64 雙緩衝放不下時改用單緩衝
    Serial.println("double buffer alloc failed, fallback to single buffer");
    delete display;
    cfg.double_buff = false;
    display = new MatrixPanel_I2S_DMA(cfg);
    display->begin();
  }
  display->setBrightness8(brightness);
  display->clearScreen();
  display->flipDMABuffer();
  display->clearScreen();
}

static void showText(const char *line1, const char *line2 = nullptr, uint16_t color = 0xFFE0) {
  display->clearScreen();
  display->setTextWrap(false);
  display->setTextSize(1);
  display->setTextColor(color);
  display->setCursor(2, 2);
  display->print(line1);
  if (line2) {
    display->setTextColor(0x8410);
    display->setCursor(2, 14);
    display->print(line2);
  }
  display->flipDMABuffer();
}

static void drawFrame() {
  for (int y = 0; y < FRAME_H; y++) {
    const uint16_t *row = &frame[y * FRAME_W];
    for (int x = 0; x < FRAME_W; x++) {
      display->drawPixel(x, y, row[x]);
    }
  }
  display->flipDMABuffer();
}

// ---------- 設定 ----------

static void loadSettings() {
  prefs.begin("scoreboard", true);
  String s = prefs.getString("server", DEFAULT_SERVER_URL);
  prefs.end();
  strlcpy(serverUrl, s.c_str(), sizeof(serverUrl));
}

static void saveSettings() {
  prefs.begin("scoreboard", false);
  prefs.putString("server", serverUrl);
  prefs.end();
}

static void setupWifi() {
  WiFiManager wm;
  pinMode(PIN_BTN_RESET, INPUT_PULLUP);
  // 開機後 2 秒內按住 BOOT = 清除 WiFi 設定。
  // 不能在接上電源「之前」就按著：BOOT 是 GPIO0，開機那一刻按著 ESP32 會進入燒錄模式
  bool reset = false;
  for (uint32_t t0 = millis(); millis() - t0 < 2000 && !reset; delay(20)) reset = digitalRead(PIN_BTN_RESET) == LOW;
  if (reset) {
    showText("RESET WIFI");
    wm.resetSettings();
    delay(1000);
  }

  WiFiManagerParameter serverParam("server", "看板伺服器網址 (例: http://192.168.1.100:8080)", serverUrl,
                                   sizeof(serverUrl) - 1);
  wm.addParameter(&serverParam);
  bool shouldSave = false;
  wm.setSaveParamsCallback([&]() { shouldSave = true; });
  wm.setConfigPortalTimeout(300);

  char apName[32];
  snprintf(apName, sizeof(apName), "Scoreboard-%04X", (uint16_t)(ESP.getEfuseMac() & 0xFFFF));
  showText("CONNECTING", apName, 0x07FF);
  wm.setAPCallback([&](WiFiManager *) { showText("WiFi SETUP", apName, 0x07FF); });

  if (!wm.autoConnect(apName)) {
    showText("WiFi FAIL", "rebooting");
    delay(3000);
    ESP.restart();
  }
  if (shouldSave) {
    strlcpy(serverUrl, serverParam.getValue(), sizeof(serverUrl));
    // 去掉尾端斜線
    size_t n = strlen(serverUrl);
    while (n > 0 && serverUrl[n - 1] == '/') serverUrl[--n] = 0;
    saveSettings();
  }
  showText("WiFi OK", WiFi.localIP().toString().c_str(), 0x07E0);
  delay(800);
}

// ---------- OTA 無線更新 ----------

static void drawOtaProgress(unsigned int done, unsigned int total) {
  static int lastPct = -1;
  int pct = total ? (int)((uint64_t)done * 100 / total) : 0;
  if (pct == lastPct) return;
  lastPct = pct;
  char line[16];
  snprintf(line, sizeof(line), "%d%%", pct);
  display->clearScreen();
  display->setTextColor(0x07FF);
  display->setCursor(2, 2);
  display->print("OTA UPDATE");
  display->setTextColor(0xFFFF);
  display->setCursor(2, 14);
  display->print(line);
  // 進度條
  const int x = 2, y = 30, w = FRAME_W - 4, h = 6;
  display->drawRect(x, y, w, h, 0x8410);
  display->fillRect(x + 1, y + 1, (w - 2) * pct / 100, h - 2, 0x07E0);
  display->flipDMABuffer();
}

static void setupOta() {
  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.onStart([]() {
    Serial.println("OTA start");
    drawOtaProgress(0, 1);
  });
  ArduinoOTA.onProgress(drawOtaProgress);
  ArduinoOTA.onEnd([]() { showText("OTA DONE", "rebooting...", 0x07E0); });
  ArduinoOTA.onError([](ota_error_t err) {
    const char *msg = err == OTA_AUTH_ERROR      ? "wrong password"
                      : err == OTA_BEGIN_ERROR   ? "begin failed"
                      : err == OTA_CONNECT_ERROR ? "connect failed"
                      : err == OTA_RECEIVE_ERROR ? "receive failed"
                                                 : "end failed";
    Serial.printf("OTA error %u: %s\n", err, msg);
    showText("OTA FAIL", msg, 0xF800);
    holdUntil = millis() + 5000;  // 錯誤訊息停 5 秒再回到比分畫面
  });
  ArduinoOTA.begin();
  Serial.printf("OTA ready: %s.local (%s)\n", OTA_HOSTNAME, WiFi.localIP().toString().c_str());
}

// ---------- 整點跑馬燈 ----------

static void marqueeOnHeaders(long inMs, const String &id) {
  if (id.isEmpty() || inMs < 0) {
    marqueeDue = false;
    return;
  }
  if (id == marqueeLastId) return;
  marqueePendingId = id;
  marqueeDueAt = millis() + (uint32_t)inMs;  // 每次回應都重新校正倒數
  marqueeDue = true;
}

// 時間到就下載長圖並捲動，捲完回傳 true（呼叫端要重新抓畫面）
static bool runMarquee() {
  if (!marqueeDue || (int32_t)(millis() - marqueeDueAt) < 0) return false;
  marqueeDue = false;
  marqueeLastId = marqueePendingId;
  if ((int32_t)(millis() - marqueeDueAt) > 60000) return false;  // 晚太久就不跑了

  HTTPClient http;
  String url = String(serverUrl) + "/marquee.bin?id=" + marqueeLastId;
  if (!http.begin(url)) return false;
  const char *keys[] = {"X-Marquee-Step-Ms", "X-Marquee-Repeat"};
  http.collectHeaders(keys, 2);
  http.setTimeout(6000);
  int code = http.GET();
  int size = http.getSize();
  const int col = FRAME_H * 2;  // 長圖一欄的位元組數
  if (code != 200 || size < FRAME_W * col || size % col || size > 512 * 1024) {
    Serial.printf("marquee: GET %s -> %d (size %d)\n", url.c_str(), code, size);
    http.end();
    return false;
  }
  uint16_t *strip = (uint16_t *)(psramFound() ? ps_malloc(size) : malloc(size));
  if (!strip) {
    Serial.println("marquee: out of memory");
    http.end();
    return false;
  }
  uint8_t *buf = reinterpret_cast<uint8_t *>(strip);
  WiFiClient *stream = http.getStreamPtr();
  size_t got = 0;
  uint32_t t0 = millis();
  while (got < (size_t)size && millis() - t0 < 6000) {
    size_t avail = stream->available();
    if (avail) {
      got += stream->readBytes(buf + got, min(avail, (size_t)size - got));
    } else {
      delay(1);
    }
  }
  int stepMs = constrain((int)http.header("X-Marquee-Step-Ms").toInt(), 5, 200);
  int repeat = constrain((int)http.header("X-Marquee-Repeat").toInt(), 1, 10);
  http.end();
  if (got != (size_t)size) {
    free(strip);
    return false;
  }

  const int w = size / col;
  Serial.printf("marquee: %s, %d px wide, %d ms/step x%d\n", marqueeLastId.c_str(), w, stepMs, repeat);
  for (int r = 0; r < repeat; r++) {
    uint32_t next = millis();
    for (int off = 0; off <= w - FRAME_W; off++) {
      for (int y = 0; y < FRAME_H; y++) {
        const uint16_t *row = &strip[y * w + off];
        for (int x = 0; x < FRAME_W; x++) display->drawPixel(x, y, row[x]);
      }
      display->flipDMABuffer();
      ArduinoOTA.handle();
      chimeLoop(serverUrl);  // 報時和跑馬燈同時開始，聲音在背景播放
      next += stepMs;
      int32_t wait = (int32_t)(next - millis());
      if (wait > 0) delay(wait);
    }
  }
  free(strip);
  return true;
}

// ---------- 網路 ----------

// 抓第 p 頁畫面到 frame[]，成功回傳 true，並更新總頁數 / 停留時間 / 重抓間隔 / 亮度
static bool fetchFrame(int p, uint32_t &holdMs) {
  HTTPClient http;
  String url = String(serverUrl) + "/frame?page=" + p;
  if (!http.begin(url)) return false;
  const char *keys[] = {"X-Pages",    "X-Hold-Ms",  "X-Brightness",   "X-Refresh-Ms", "X-Chime-In",
                        "X-Chime-Id", "X-Chime-Ver", "X-Chime-Volume", "X-Marquee-In", "X-Marquee-Id"};
  http.collectHeaders(keys, 10);
  http.setTimeout(6000);

  int code = http.GET();
  const size_t need = sizeof(frame);
  if (code != 200 || http.getSize() != (int)need) {
    Serial.printf("GET %s -> %d (size %d)\n", url.c_str(), code, http.getSize());
    http.end();
    return false;
  }

  // 直接讀進 frame[]；只有完整收到才會呼叫 drawFrame()，所以讀到一半失敗不會顯示殘缺畫面
  uint8_t *buf = reinterpret_cast<uint8_t *>(frame);
  WiFiClient *stream = http.getStreamPtr();
  size_t got = 0;
  uint32_t t0 = millis();
  while (got < need && millis() - t0 < 6000) {
    size_t avail = stream->available();
    if (avail) {
      got += stream->readBytes(buf + got, min(avail, need - got));
    } else {
      delay(1);
    }
  }
  if (got != need) {
    http.end();
    return false;
  }
  // ESP32 是 little-endian，與伺服器輸出的 RGB565 位元組順序一致，不用再轉換

  pageCount = max(1L, http.header("X-Pages").toInt());
  holdMs = max(1000L, http.header("X-Hold-Ms").toInt());
  long r = http.header("X-Refresh-Ms").toInt();
  refreshMs = r > 0 ? max(1000L, r) : 0;
  String chimeIn = http.header("X-Chime-In");
  chimeOnHeaders(chimeIn.length() ? chimeIn.toInt() : -1, http.header("X-Chime-Id"), http.header("X-Chime-Ver"),
                 http.hasHeader("X-Chime-Volume") ? http.header("X-Chime-Volume").toInt() : -1);
  String marqueeIn = http.header("X-Marquee-In");
  marqueeOnHeaders(marqueeIn.length() ? marqueeIn.toInt() : -1, http.header("X-Marquee-Id"));
  int b = http.header("X-Brightness").toInt();
  if (b > 0 && b != brightness) {
    brightness = b;
    display->setBrightness8(brightness);
  }
  http.end();
  return true;
}

// ---------- 按鈕 ----------

static bool pressed(int pin) {
  if (pin < 0) return false;
  static uint32_t last[48] = {0};
  if (digitalRead(pin) == LOW && millis() - last[pin] > 300) {
    last[pin] = millis();
    return true;
  }
  return false;
}

// ---------- 主程式 ----------

void setup() {
  Serial.begin(115200);
  setupDisplay();
  showText("SCOREBOARD", "booting...");
  loadSettings();
  setupWifi();
  WiFi.setSleep(false);  // 關掉 WiFi 省電模式，抓畫面和無線更新都比較穩
  setupOta();
  chimeSetup();
  if (PIN_BTN_NEXT >= 0) pinMode(PIN_BTN_NEXT, INPUT_PULLUP);
  if (PIN_BTN_PAUSE >= 0) pinMode(PIN_BTN_PAUSE, INPUT_PULLUP);
  Serial.printf("server: %s\n", serverUrl);
}

void loop() {
  ArduinoOTA.handle();  // 隨時接受無線更新
  chimeLoop(serverUrl);  // 整點報時：時間到就在背景播放
  if (runMarquee()) {    // 整點跑馬燈：跑完重新抓目前這頁
    page = shown;
    holdUntil = 0;
    return;
  }

  if (pressed(PIN_BTN_NEXT)) {
    page = (page + 1) % pageCount;
    holdUntil = 0;
  }
  if (pressed(PIN_BTN_PAUSE)) {
    paused = !paused;
    page = shown;
  }

  if (millis() < holdUntil) {
    if (refreshMs && millis() >= nextRefresh) {
      // 停留期間重抓同一頁：只更新比分，不換頁、也不延長停留時間
      uint32_t ignored;
      if (fetchFrame(shown, ignored)) drawFrame();
      nextRefresh = millis() + refreshMs;
    }
    delay(20);
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    showText("WiFi LOST", "reconnecting");
    WiFi.reconnect();
    holdUntil = millis() + 5000;
    return;
  }

  uint32_t holdMs = 8000;
  if (fetchFrame(page, holdMs)) {
    fails = 0;
    shown = page;
    drawFrame();
    if (paused) {
      holdMs = min<uint32_t>(holdMs, 10000);  // 暫停時仍定期重抓同一頁，比分才會即時更新
    } else {
      page = (page + 1) % pageCount;
    }
    holdUntil = millis() + holdMs;
    nextRefresh = millis() + refreshMs;
  } else {
    if (++fails >= FAILS_BEFORE_ERROR) showText("NO SERVER", serverUrl, 0xF800);
    holdUntil = millis() + 3000;
  }
}
