# newmean_pii example

Type text, pick a model size and a redaction mode, and mask the personal information in it on the device. The model
(nano by default here, 3.4 MB with the tokenizer) is downloaded once from Hugging Face and checked against its pinned
SHA-256.

```bash
flutter run                                   # a connected device or a simulator
flutter build apk --debug                     # Android (arm64-v8a, armeabi-v7a, x86_64)
flutter build ios --simulator --no-codesign   # iOS simulator
```
