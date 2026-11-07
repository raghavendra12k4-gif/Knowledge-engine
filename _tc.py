from tests.test_chunker import *
test_chunker_respects_headings_and_paths()
test_chunker_never_splits_code_block()
test_chunker_never_splits_table()
test_chunker_overlap_and_sentence_split()
test_docx_paragraph_chunking_locator()
print('chunker ok')
