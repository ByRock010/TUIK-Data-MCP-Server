# Midterm raporu V2

V2, mevcut teslim raporunun yalnızca yatay hizalama ve isim ayırıcıları düzeltilmiş kopyasıdır. Rapor içeriği ve 11 Kasım 2026 kesim tarihi korunmuştur.

- 155 mm metin genişliği korunarak sol ve sağ kenar boşlukları 27,5 mm yapıldı. Başlıklar, tablolar, şekiller ve kapak bu metin alanıyla birlikte ortalanır.
- Kapaktaki öğrenci satırları `İsim Soyisim | numara` biçimine getirildi. Raporda başka em dash bulunmadığı doğrulandı.
- Orijinal rapor ve kaynakları ayrı tutuldu. `styles/` dosyaları değiştirilmedi; kenar boşluğu düzeltmesi `report.tex` içinde uygulanır.

## Derleme

Bu klasörde `xelatex report.tex`, `bibtex report`, ardından iki kez `xelatex report.tex` çalıştırın. Overleaf kullanırken klasörün tamamını yükleyip ana dosyayı `report.tex`, derleyiciyi XeLaTeX seçin.

Stil dosyalarının kaynağı ve orijinal dağıtım belgeleri `template-origin/` altında korunmuştur. İçerik ve önceki teknik doğrulama ayrıntıları için önceki sürümün `../midterm-report/README.md` dosyasına bakın.
