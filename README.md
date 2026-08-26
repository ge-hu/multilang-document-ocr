# 多语言文档OCR助手

Windows 10/11 64位本地程序，用于从PDF、JPG和PNG中提取可编辑文字，再按A4连续排版导出PDF。

## 试用版功能

- 优先直接提取PDF文字层；扫描页和图片才调用OCR
- 内置之前使用的23种欧洲语言及简体中文，共24种OCR语言包
- 可多选语言、强制OCR、调整扫描分辨率
- 提取结果可人工编辑，并可导出UTF-8 TXT
- A4 PDF默认5.5pt、8mm安全边距，连续排版，不按语言强制换页
- 文件完全在本机处理，不上传服务器

## 下载Windows便携版

进入仓库的 **Actions → Build Windows Portable → 最新成功任务 → Artifacts**，下载 `MultilangOCR-Windows-x64`，解压后双击 `MultilangOCR.exe`。

## 本地开发

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

开发环境需自行安装Tesseract 5及相应语言包；GitHub Actions生成的便携版已全部内置。

## 当前限制

- 首版仅支持PDF、JPG、JPEG和PNG。
- “选择语言”用于提高OCR识别准确度，不等同于自动删除未选择语言。
- 一次选择过多语言会明显降低扫描件OCR速度；建议只选文件中实际出现的语言。
- OCR文字必须人工复核，尤其是温度、电压、型号、警告语和特殊符号。

## 许可证提示

本仓库暂未授予项目源码的再分发许可。第三方依赖许可见 `THIRD_PARTY_NOTICES.md`，商业发布前应保留所需许可声明。
