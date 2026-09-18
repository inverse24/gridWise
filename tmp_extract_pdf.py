import PyPDF2

pdf = r'D:\BUP_CSE_FEST_2026_Participant_Docs\BUP_CSE_FEST_2026_Preliminary_Problem_Statement_GridWise_LLM.pdf'
reader = PyPDF2.PdfReader(pdf)
print('pages=', len(reader.pages))
for i, page in enumerate(reader.pages[:12], start=1):
    text = page.extract_text() or ''
    print(f'--- PAGE {i} START ---')
    print(text[:6000])
    print(f'--- PAGE {i} END ---')
