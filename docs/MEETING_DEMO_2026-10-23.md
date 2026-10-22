# 23 Ekim 2026 — hoca toplantısı demo akışı

## Hazırlık

PR'lar henüz birleşmemişse tüm geliştirmeler `feature/7-pdf-documents` dalında bulunur.
MCP istemcisinde bu checkout'un `.venv/bin/tuik-mcp` komutunu kullanın ve istemciyi
araç listesini yeniden yükleyecek şekilde başlatın. Yedi araç görünmeli.

Tek komutla gerçek MCP oturumu ve canlı JSON raporu:

```bash
TUIK_MIN_INTERVAL_SECONDS=2 .venv/bin/python scripts/live_smoke.py --output /tmp/tuik-demo.json
```

## 5 dakikalık anlatım

1. **Kaynağı bul:** `search_tuik({"query":"işsizlik","limit":5})`.
   Resmi kaynak URL'sini ve yeniden kullanılabilen kimliği gösterin.
2. **Bülten → CSV:** `get_tuik_resource({"resource_id":"press:58347"})`.
   `related_datasets` içinden `downloadable: true` olan bir `flow:` kimliğini seçin;
   `get_tuik_data` ile `limit: 2` kullanarak satırları alın. Seçim açıkça yapılıyor.
3. **Excel → satırlar:** `get_tuik_resource({"resource_id":"press:58298"})`.
   `download_links` içinden “Tüketici fiyat endeksi ve değişim oranları” dosyasının
   `resource_id` değerini seçin. `get_tuik_data` ile `limit: 2` çağırın.
   `unit: 2025=100`, `sheet`, satırlar, `total_rows`, dipnotlar ve gerçek dosya URL'sini gösterin.
   XLS-only bülteni doğrudan göstermek için `get_tuik_data({"resource_id":"press:53899","limit":2})`:
   22 Ekim doğrulamasında 82 kaynak satırı vardı.
4. **PDF metodoloji:** `read_tuik_document({"resource_id":"press:57987","page":3,"max_chars":120})`.
   Bültenin tek PDF'si seçilir. Dönen `resolved_resource_id`, sayfa numarası ve kaynak
   URL'sini gösterin. Sonraki çağrıda **hem** `next_page` hem `next_offset` kullanın:
   metnin kalan kısmı atlanmadan aynı sayfadan okunur.
5. **PDF yayın:** “Hanehalkı Bilişim Teknolojileri Kullanım Araştırması Mikro Veri Seti, 2026”
   aramasıyla bir `publication:` kimliği bulun, `read_tuik_document` ile ilk sayfayı açın.

## Kapsamı doğru anlatma

İlk MCP MVP'si çalışıyor. Bu üç PR Excel/PDF okuma ile sınırlandırma/cache hedeflerini
uyguluyor; review ve merge bekliyor. OpenSearch için yalnızca geliştirme altyapısı
hazır. Türkçe değerlendirme seti (#11), arama uygulaması ve v0.2.0 release kapanışı
(#10) sonraki işler. OCR ve PDF'den tablo çıkarma bu kapsamda değil.

`press:54072` örneğinin ilk dosyası 22 Ekim'de TÜİK'ten 404 döndüğü için demo
örneği olarak kullanılmıyor. Canlı erişim upstream'e bağlı; tarihli raporlar o anda
neyin doğrulandığını gösterir. Gerçek dosyalarla offline tekrar için:

```bash
.venv/bin/pytest tests/test_spreadsheet_data.py tests/test_pdf_documents.py -q
```
