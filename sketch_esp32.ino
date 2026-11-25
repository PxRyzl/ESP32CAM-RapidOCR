// ================================================================
//  ESP32-CAM - Attendance Scanner (Final Version)
//  - Requires ESP32 Core 3.2.1
//  - New PWM API (ledcAttach/ledcWrite to pin)
//  - Camera-first init + PSRAM frame buffers
//  - Manual exposure control
// ================================================================

#include "esp_camera.h"
#include <WiFi.h>
#include "esp_wifi.h"
#include "esp_http_server.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ==========================================
// ⚠️ CHANGE THESE TO YOUR WIFI DETAILS ⚠️
// ==========================================
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// ========== USER PINS ==========
#define BUTTON_PIN   13
#define RED_LED      2
#define GREEN_LED    12
#define FLASH_PIN    4
#define BUZZER_PIN   14

// ========== FLASH PWM SETTINGS ==========
#define FLASH_PWM_FREQ    5000
#define FLASH_PWM_RES     8
// Adjust this based on your box/casing. 100 works well for 3D printed opaque cases.
#define FLASH_BRIGHTNESS  100

// ========== CAMERA PINS ==========
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

camera_fb_t* latest_fb = NULL;
uint32_t image_version = 0;

void setupCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.fb_location  = CAMERA_FB_IN_PSRAM;
  config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;

  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_VGA;
  config.jpeg_quality = 4;
  config.fb_count     = 1;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return;
  }

  sensor_t *s = esp_camera_sensor_get();
  s->set_exposure_ctrl(s, 0);
  s->set_aec_value(s, 200);
  s->set_gain_ctrl(s, 0);
  s->set_agc_gain(s, 5);
  s->set_ae_level(s, -2);

  Serial.println("Camera ready (manual exposure)");
}

void captureAndStore() {
  if (latest_fb != NULL) {
    esp_camera_fb_return(latest_fb);
    latest_fb = NULL;
    delay(20);
  }

  digitalWrite(RED_LED, HIGH);
  ledcWrite(FLASH_PIN, FLASH_BRIGHTNESS);
  delay(150);

  camera_fb_t* fb = NULL;
  int attempts = 0;
  while (fb == NULL && attempts < 3) {
    fb = esp_camera_fb_get();
    if (fb == NULL) {
      attempts++;
      delay(100);
      Serial.printf("Retry %d...\n", attempts);
    }
  }

  digitalWrite(RED_LED, LOW);
  ledcWrite(FLASH_PIN, 0);

  if (fb == NULL) {
    Serial.println("❌ Capture failed!");
    return;
  }

  latest_fb = fb;
  image_version++;
  Serial.printf("✅ New image! Version: %u, Size: %u bytes\n", image_version, fb->len);
}

void buzz(int times) {
  for (int i = 0; i < times; i++) {
    digitalWrite(BUZZER_PIN, HIGH);
    delay(200);
    digitalWrite(BUZZER_PIN, LOW);
    if (i < times - 1) delay(200);
  }
}

esp_err_t capture_handler(httpd_req_t *req) {
  if (latest_fb == NULL) captureAndStore();
  if (latest_fb == NULL) {
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }
  httpd_resp_set_type(req, "image/jpeg");
  char version_str[16];
  sprintf(version_str, "%u", image_version);
  httpd_resp_set_hdr(req, "X-Image-Version", version_str);
  httpd_resp_send(req, (const char*)latest_fb->buf, latest_fb->len);
  return ESP_OK;
}

esp_err_t feedback_handler(httpd_req_t *req) {
  char query[32] = {0};
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK) {
    char status[16] = {0};
    if (httpd_query_key_value(query, "status", status, sizeof(status)) == ESP_OK) {
      if (strcmp(status, "success") == 0) {
        Serial.println("✅ SUCCESS");
        digitalWrite(RED_LED, LOW);
        digitalWrite(GREEN_LED, HIGH);
        buzz(1);
        digitalWrite(GREEN_LED, LOW);
      } else if (strcmp(status, "fail") == 0) {
        Serial.println("❌ FAIL");
        digitalWrite(GREEN_LED, LOW);
        digitalWrite(RED_LED, HIGH);
        buzz(2);
        digitalWrite(RED_LED, LOW);
      }
    }
  }
  httpd_resp_set_type(req, "text/plain");
  httpd_resp_send(req, "OK", 2);
  return ESP_OK;
}

void startServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  httpd_handle_t server = NULL;
  if (httpd_start(&server, &config) == ESP_OK) {
    httpd_uri_t capture_uri = {
      .uri = "/capture", .method = HTTP_GET,
      .handler = capture_handler, .user_ctx = NULL
    };
    httpd_register_uri_handler(server, &capture_uri);

    httpd_uri_t feedback_uri = {
      .uri = "/feedback", .method = HTTP_GET,
      .handler = feedback_handler, .user_ctx = NULL
    };
    httpd_register_uri_handler(server, &feedback_uri);

    Serial.println("Web server started!");
  }
}

void connectToWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_POWER_13dBm);
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ WiFi connected!");
    Serial.print("📡 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n❌ WiFi failed! Rebooting...");
    delay(1000);
    ESP.restart();
  }
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  Serial.begin(115200);

  pinMode(RED_LED, OUTPUT);
  pinMode(GREEN_LED, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  digitalWrite(RED_LED, LOW);
  digitalWrite(GREEN_LED, LOW);
  digitalWrite(BUZZER_PIN, LOW);

  // New PWM API — works on 3.2.1
  ledcAttach(FLASH_PIN, FLASH_PWM_FREQ, FLASH_PWM_RES);
  ledcWrite(FLASH_PIN, 0);
  Serial.println("Flash PWM ready");

  for (int i = 0; i < 3; i++) {
    digitalWrite(RED_LED, HIGH); delay(200);
    digitalWrite(RED_LED, LOW);  delay(200);
  }

  Serial.println("Initializing camera...");
  setupCamera();
  delay(200);

  connectToWiFi();

  Serial.println("Taking initial photo...");
  captureAndStore();
  startServer();
}

void loop() {
  if (digitalRead(BUTTON_PIN) == LOW) {
    delay(50);
    if (digitalRead(BUTTON_PIN) == LOW) {
      Serial.println("🔘 Button Press detected!");
      captureAndStore();
      while (digitalRead(BUTTON_PIN) == LOW) delay(10);
      Serial.println("🔘 Button released.");
    }
  }
  delay(10);
}
