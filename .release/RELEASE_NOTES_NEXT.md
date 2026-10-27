# PDFium from Chromium 156

Updates PDFium to `chromium/8078` (Chromium 156.0.8078.47, the revision Chrome Stable ships), commit
`2f22dfe030de`.

## Valkyrian Labs APT repository

The packages are now published to `apt.valkyrianlabs.com`, the repository that also serves Vaulthalla and the
libpqxx SDK, signed by the Valkyrian Labs LLC key (`494D 71A1 EEDD F87B D48E  5048 6D64 CB7B DFC3 0EC6`).
`apt.vaulthalla.sh` serves the same index, so existing sources keep working:

```sh
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://apt.valkyrianlabs.com/pubkey.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/valkyrianlabs.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/valkyrianlabs.gpg] https://apt.valkyrianlabs.com stable main" \
  | sudo tee /etc/apt/sources.list.d/valkyrianlabs.list
sudo apt update && sudo apt install libpdfium-dev
```
