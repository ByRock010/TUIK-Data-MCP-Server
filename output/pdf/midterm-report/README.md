# MCP Server for TÜİK Data — Midterm raporu

Bu klasör, verilen `report-v2.tex` şablonunun bölüm yapısını kullanan İngilizce raporun düzenlenebilir LaTeX kaynaklarını içerir. PDF, üst klasörde `Bayrakcioglu_Ahmet_Baha_and_Acer_Okkes_Berkay_MidtermReport.pdf` adıyla sunulur.

Güncellenmiş PDF **34 sayfa, yaklaşık 117 KiB** boyutundadır; 20 MB sınırının altındadır. Tüm sayfalar görüntülenerek yerleşim kontrolü yapıldı. Derlemede taşan kutu, eksik karakter veya çözülemeyen atıf bulunmuyor.

## İçerik ve dayanak

- Öğrenciler: Ahmet Baha Bayrakçıoğlu (2022400051) ve Ökkeş Berkay Acer (2022400066).
- Danışman alanı: Can Özturan. Kullanıcının verdiği isim, şablondaki `Advisor` alanına yerleştirildi; akademik unvan eklenmedi.
- Rapor kesim tarihi: 11 Kasım 2026. Yerel revizyon `e3e991b` ile birlikte GitHub development merge commit’i `759c26e` (PR #77) incelendi. v0.3.0’ın 5 Kasım’da yayımlandığı GitHub release kaydından doğrulandı. Hibrit arama tamamlanan işler arasındadır; açık teknik follow-up’lar #78–#80’dir. Tarihler ekipçe kesinleştirilmiş tarih değil, rapordaki öneri takvimidir.
- Tamamlanmış işler kaynak kodu, kayıtlı deneyler ve doğrulama belgelerine dayandırıldı. Kalan takvim öneridir; gerçekleşmiş çalışma olarak sunulmaz.
- Rapor hazırlanırken mevcut çevrimdışı testler yeniden çalıştırıldı: **718 passed, 21 skipped, 6 deselected (45.23 s)**. Canlı TÜİK ve gerçek OpenSearch sonuçları 5 Kasım tarihli kayıtlı kanıtlardır; bu rapor çalışmasında yeniden yapılmış gibi yazılmadı.
- Ekibin bildirdiği sınırlı elle denemeler nitel kontrol olarak eklendi. Leksik deneyin 1.390 etiketi Codex’e, hibrit deneydeki 299 yeni etiket Claude’a ve kendi bağımlı kaynak incelemelerine aittir; bağımsız insan değerlendirmesi veya ölçülmüş kullanılabilirlik sonucu iddia edilmez.

## Derleme

Overleaf'e bu klasördeki `report.tex`, `references.bib` ve `styles/` klasörünü birlikte yükleyin. Ana dosya `report.tex`; derleyici olarak XeLaTeX veya pdfLaTeX kullanılabilir. Yerel bir TeX dağıtımıyla:

```sh
pdflatex report.tex
bibtex report
pdflatex report.tex
pdflatex report.tex
```

Tectonic ile `tectonic report.tex` yeterlidir. İlk çalıştırmada paket indirmesi gerekebilir. Diyagramlar TikZ ile kaynak dosyasında çizilir; ayrı resim dosyası gerekmez.

## Stil dosyalarının kaynağı

Kullanıcının verdiği `.tex`, `styles/fbe_tez.sty` ve `styles/fbe_tez_v11.bst` dosyalarını çağırıyor, ancak bu dosyalar eklerde yoktu. Bu iki dosya, Boğaziçi Üniversitesi FBE'nin resmî şablon arşivinden değiştirilmeden alındı:

https://mediastore.cc.bogazici.edu.tr/web/userfiles/files/fbe_adv_thesis_template_2022_2.zip

Kaynak sayfa: https://fbe.bogazici.edu.tr/en/pages/thesis-formatting-instructions/2847

Resmî arşivin değiştirilmemiş bir kopyası, dokümantasyonu ve dağıtım bildirimleriyle birlikte `template-origin/` klasöründe korunmuştur.

Arşiv içindeki konum: `fbe_adv_thesis_template_2022/styles/`. Stil dosyaları Feza Kerestecioğlu'nun telif ve dağıtım bildirimini içerir; bildirim korundu. Bu dosyaları yeniden dağıtırken resmî arşivdeki dokümantasyonu ve kullanım koşullarını da koruyun. Bölüm başlıkları, kenar boşlukları, satır aralığı ve kaynakça biçimi verilen şablonun stil ailesine dayanır. Moodle'daki asıl şablon paketiniz farklı bir stil sürümü içeriyorsa, son derlemede oradaki iki dosyayı kullanabilirsiniz.

## Teslim öncesi

İçeriği ve özellikle gelecek çalışma planını ekip olarak gözden geçirin; danışman onayını alın. Duyuruda midterm için bir yerde 17 Ekim, ayrıntılı teslim satırında ise **17 Kasım 2026, 23:59** yazıyor. Moodle'daki güncel teslim tarihini esas alarak doğrulayın. Duyuru final tarihi için de kesinleşmemiş ifadeler içerdiğinden rapordaki Aralık planı kesin teslim tarihi iddiası değildir.

İstenen teslim dosyası PDF'dir ve 20 MB sınırının altında olmalıdır. Yükleme ve danışmana e-posta gönderimi kullanıcıya bırakılmıştır. LaTeX kaynakları üzerinde değişiklik yaparsanız PDF'yi yeniden derleyin.

## GitHub doğrulamasıyla yapılan düzeltme

İlk rapor yalnızca daha eski yerel snapshot’a dayanıyordu. Kullanıcının doğrulama sorusu üzerine merge edilmiş PR’lar, kapalı/açık issue’lar, #77’nin gerçek kaynak kodu ve yayımlanmış release doğrudan kontrol edildi. Hibrit aramayı geleceğe bırakan ve v0.3.0’ı yayımlanmamış gösteren ifadeler düzeltildi. Yerel proje checkout’u değiştirilmedi; GitHub kaynağı geçici klasörde incelendi.

GitHub merge commit’inin (`759c26e`) geçici kopyasında çevrimdışı testler yeniden çalıştırıldı: **742 passed, 22 skipped, 6 deselected (43.62 s)**. Canlı/cluster testleri bu düzeltme sırasında tekrarlanmadı.
