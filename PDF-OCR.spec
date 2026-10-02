from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = collect_data_files('paddlex') + copy_metadata('paddleocr') + copy_metadata('paddlex')
a = Analysis(['mac_app.py'], pathex=[], binaries=[], datas=datas,
             hiddenimports=['paddleocr._api_client.async_client', 'paddleocr._api_client.models'],
             hookspath=[], runtime_hooks=[], excludes=['torch', 'tensorflow', 'paddle', 'IPython', 'matplotlib'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PDF-OCR', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          argv_emulation=False, target_arch='arm64')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PDF-OCR')
app = BUNDLE(coll, name='PDF-OCR.app', icon='assets/app-icon.icns', bundle_identifier='local.pdfocr.workflow',
             info_plist={'NSHighResolutionCapable': True, 'CFBundleDocumentTypes': [
                 {'CFBundleTypeName': 'PDF Document', 'CFBundleTypeRole': 'Viewer',
                  'LSItemContentTypes': ['com.adobe.pdf'], 'CFBundleTypeExtensions': ['pdf']} ]})
