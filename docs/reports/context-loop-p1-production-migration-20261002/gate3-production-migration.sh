#!/bin/sh
set -eu
python3 - <<'MF_APPROVED_PRODUCTION'
import base64, sys, types
_support = types.ModuleType('rehearsal_runner')
exec(compile(base64.b64decode('IiIiUnVuIDAwMy8wMDQgb25seSBvbiBhIG5ld2x5LWNyZWF0ZWQsIGxhYmVsbGVkLCBuZXR3b3JrLWlzb2xhdGVkIGNvbnRhaW5lci4iIiIKaW1wb3J0IGJhc2U2NAppbXBvcnQgaGFzaGxpYgppbXBvcnQganNvbgppbXBvcnQgb3MKaW1wb3J0IHJlCmltcG9ydCBzaHV0aWwKaW1wb3J0IHN1YnByb2Nlc3MKaW1wb3J0IHRlbXBmaWxlCmltcG9ydCB0aW1lCmltcG9ydCB1dWlkCmZyb20gZGF0ZXRpbWUgaW1wb3J0IGRhdGV0aW1lLCB0aW1lem9uZQpmcm9tIHBhdGhsaWIgaW1wb3J0IFBhdGgKClBST0RVQ1RJT04gPSAibW9vZGZsb3ctcHJvZHVjdGlvbi1wb3N0Z3Jlcy0xIgpJTUFHRSA9ICJwZ3ZlY3Rvci9wZ3ZlY3RvcjpwZzE2LXRyaXhpZUBzaGEyNTY6Yzg0ODM1NTVjZTQ4MTAxODcyZjg4OGMxZGY4YTg5NWZmNjg5ZDZjN2M3YTVmN2FjMjY2NDc1ZjlkZmU4OWUwYiIKQkFDS1VQUyA9IFBhdGgoIi9zZWN1cmUvbW9vZGZsb3cvYmFja3VwcyIpCk1BTklGRVNUID0gQkFDS1VQUyAvICJtYW5pZmVzdHMvMjAyNjEwMDFUMjM0MTAzWi0zYTExMjM3Yy5qc29uIgpSRVBPUlRTID0gUGF0aCgiL3NlY3VyZS9tb29kZmxvdy9taWdyYXRpb24tcmVoZWFyc2FscyIpCkVYUEVDVEVEX0hJU1RPUllfUk9XUyA9IDMyOApFWFBFQ1RFRF9EQVRBQkFTRV9CWVRFUyA9IDUzODk0MTY3CkxBQkVMID0gIm1vb2RmbG93LmNvbnRleHQtbG9vcC1yZWhlYXJzYWwiCklOTkVSID0gIi90bXAvbW9vZGZsb3ctY29udGV4dC1sb29wIgpNSUdSQVRJT05TID0gewogICAgIjAwM19jb250ZXh0X2xvb3BfaGlzdG9yeS5zcWwiOiB7CiAgICAgICAgImJhc2U2NCI6ICJMUzBnVG1WM0lHVjJaVzUwY3lCb1lYWmxJR0VnWkdWMFpYSnRhVzVwYzNScFl5QnlaWEJzWVhrZ2EyVjVJR0Z1WkNCemRHRmliR1VnWkdGMFlXSmhjMlVnYjNKa1pYSXVDaTB0SUU1dklGVlFSRUZVUlNCdlppQnBiVzExZEdGaWJHVWdaWFpsYm5RZ1ltOWthV1Z6TENCdWIzSWdZbkp2WVdSbGNpQmphR1ZqYTNCdmFXNTBJR1JsYzJWeWFXRnNhWHBoZEdsdmJpNEtRVXhVUlZJZ1ZFRkNURVVnWVdkbGJuUmZhR2x6ZEc5eWVWOWxkbVZ1ZENCQlJFUWdRMDlNVlUxT0lFbEdJRTVQVkNCRldFbFRWRk1nWlhabGJuUmZhMlY1SUZaQlVrTklRVklvTVRZd0tUc0tRVXhVUlZJZ1ZFRkNURVVnWVdkbGJuUmZhR2x6ZEc5eWVWOWxkbVZ1ZENCQlJFUWdRMDlNVlUxT0lFbEdJRTVQVkNCRldFbFRWRk1nWlhabGJuUmZjMlZ4SUVKSlIxTkZVa2xCVERzS1ExSkZRVlJGSUZWT1NWRlZSU0JKVGtSRldDQkpSaUJPVDFRZ1JWaEpVMVJUSUdGblpXNTBYMmhwYzNSdmNubGZaWFpsYm5SZmEyVjVDaUFnSUNCUFRpQmhaMlZ1ZEY5b2FYTjBiM0o1WDJWMlpXNTBLR1YyWlc1MFgydGxlU2s3Q2tOU1JVRlVSU0JWVGtsUlZVVWdTVTVFUlZnZ1NVWWdUazlVSUVWWVNWTlVVeUJoWjJWdWRGOW9hWE4wYjNKNVgyVjJaVzUwWDNObGNRb2dJQ0FnVDA0Z1lXZGxiblJmYUdsemRHOXllVjlsZG1WdWRDaGxkbVZ1ZEY5elpYRXBPd3BEVWtWQlZFVWdTVTVFUlZnZ1NVWWdUazlVSUVWWVNWTlVVeUJoWjJWdWRGOW9hWE4wYjNKNVgzUmhjMnRmYzJWeENpQWdJQ0JQVGlCaFoyVnVkRjlvYVhOMGIzSjVYMlYyWlc1MEtIUmhjMnRmYVdRc0lHVjJaVzUwWDNObGNTazdDZz09IiwKICAgICAgICAic2hhMjU2IjogImRiMjE3NTJiY2I4NmFmZmFlMDM5NjM0MzQyZTViNmRmOGVjMTEzMDUzYWE1ZWNiMjEzZjA3Yzg1NGFjYjY3YjMiCiAgICB9LAogICAgIjAwNF9jb250aW51aXR5X2V4ZWN1dGlvbi5zcWwiOiB7CiAgICAgICAgImJhc2U2NCI6ICJMUzBnVFdGdWRXRnNJSEJ5YjJSMVkzUnBiMjRnYldsbmNtRjBhVzl1SUc5dWJIa3VJRk4wWVhKMGRYQWdiWFZ6ZENCdVpYWmxjaUJsZUdWamRYUmxJSFJvYVhNZ1ptbHNaUzRLUTFKRlFWUkZJRlJCUWt4RklFbEdJRTVQVkNCRldFbFRWRk1nWVdkbGJuUmZaWGhsWTNWMGFXOXVJQ2dLSUNBZ0lHVjRaV04xZEdsdmJsOXJaWGtnVkVWWVZDQlFVa2xOUVZKWklFdEZXU3dLSUNBZ0lHRjFkR2hmYzJOdmNHVWdWRVZZVkNCT1QxUWdUbFZNVEN3S0lDQWdJRzkzYm1WeUlGUkZXRlFzQ2lBZ0lDQm1aVzVqWlNCQ1NVZEpUbFFnVGs5VUlFNVZURXdnUkVWR1FWVk1WQ0F3TEFvZ0lDQWdjbVYyYVhOcGIyNGdRa2xIU1U1VUlFNVBWQ0JPVlV4TUlFUkZSa0ZWVEZRZ01Dd0tJQ0FnSUdWNGNHbHlaWE5mWVhRZ1JFOVZRa3hGSUZCU1JVTkpVMGxQVGlCT1QxUWdUbFZNVENCRVJVWkJWVXhVSURBS0tUc0siLAogICAgICAgICJzaGEyNTYiOiAiNTYyMjcyM2MzNDFmNjc4MjA4NTAzOWFkMmQwODcyZDM1ZWQ5MzMwM2E2ZjExMTRkNzg3OGU5NmViMGEzODhkMiIKICAgIH0KfQoKQ09SRSA9ICgiYWdlbnRfc2Vzc2lvbiIsICJhZ2VudF90YXNrIiwgImFnZW50X2xvbmdfdGVybV9tZW1vcnkiLAogICAgICAgICJjaGVja3BvaW50cyIsICJjaGVja3BvaW50X2Jsb2JzIiwgImNoZWNrcG9pbnRfd3JpdGVzIikKT0xEX0xFREdFUiA9IFsiMDAxX2FnZW50X2NvbnRpbnVpdHkuc3FsIiwgIjAwMl9sb25nX3Rlcm1fbWVtb3J5LnNxbCJdCk5FV19MRURHRVIgPSBPTERfTEVER0VSICsgbGlzdChNSUdSQVRJT05TKQoKCmNsYXNzIFN0b3AoUnVudGltZUVycm9yKToKICAgIHBhc3MKCgpkZWYgcnVuKGFyZ3MsIGRhdGE9Tm9uZSwgc3RyZWFtPU5vbmUsIHRpbWVvdXQ9OTAsIGNoZWNrPVRydWUpOgogICAgcmVzdWx0ID0gc3VicHJvY2Vzcy5ydW4oYXJncywgaW5wdXQ9ZGF0YSwgc3RkaW49c3RyZWFtLCBjYXB0dXJlX291dHB1dD1UcnVlLAogICAgICAgICAgICAgICAgICAgICAgICAgICAgdGltZW91dD10aW1lb3V0LCBjaGVjaz1GYWxzZSkKICAgIGlmIGNoZWNrIGFuZCByZXN1bHQucmV0dXJuY29kZToKICAgICAgICByYWlzZSBTdG9wKCJjb21tYW5kX2ZhaWxlZF9leGl0XyIgKyBzdHIocmVzdWx0LnJldHVybmNvZGUpKQogICAgcmV0dXJuIHJlc3VsdAoKCmRlZiB0ZXh0KGFyZ3MsICoqa3dhcmdzKToKICAgIHJldHVybiBydW4oYXJncywgKiprd2FyZ3MpLnN0ZG91dC5kZWNvZGUoInV0Zi04Iikuc3RyaXAoKQoKCmRlZiBjaGVja2VkX2JhY2t1cChtYW5pZmVzdF9wYXRoPU1BTklGRVNULCByb290PUJBQ0tVUFMpOgogICAgaWYgKG1hbmlmZXN0X3BhdGguaXNfc3ltbGluaygpIG9yIG5vdCBtYW5pZmVzdF9wYXRoLmlzX2ZpbGUoKQogICAgICAgICAgICBvciBub3QgbWFuaWZlc3RfcGF0aC5yZXNvbHZlKCkuaXNfcmVsYXRpdmVfdG8oKHJvb3QvIm1hbmlmZXN0cyIpLnJlc29sdmUoKSkpOgogICAgICAgIHJhaXNlIFN0b3AoInVuc2FmZV9tYW5pZmVzdCIpCiAgICBpdGVtID0ganNvbi5sb2FkcyhtYW5pZmVzdF9wYXRoLnJlYWRfdGV4dChlbmNvZGluZz0idXRmLTgiKSkKICAgIGlmIGl0ZW0uZ2V0KCJzdGF0dXMiKSAhPSAiY29tcGxldGUiIG9yIGl0ZW0uZ2V0KCJyZXN0b3JlX3ZlcmlmaWVkIikgaXMgbm90IFRydWU6CiAgICAgICAgcmFpc2UgU3RvcCgiYmFja3VwX25vdF92ZXJpZmllZCIpCiAgICBpZiBpdGVtWyJwb3N0Z3Jlc19tZXRhZGF0YSJdWyJkYXRhYmFzZSJdICE9ICJtb29kZmxvd19hZ2VudCI6CiAgICAgICAgcmFpc2UgU3RvcCgiYmFja3VwX2RhdGFiYXNlX21pc21hdGNoIikKICAgIGlmIGl0ZW1bInBvc3RncmVzX21ldGFkYXRhIl1bImRhdGFiYXNlX3NpemVfYnl0ZXMiXSAhPSBFWFBFQ1RFRF9EQVRBQkFTRV9CWVRFUzoKICAgICAgICByYWlzZSBTdG9wKCJiYWNrdXBfc291cmNlX3NpemVfY2hhbmdlZCIpCiAgICBkdW1wID0gUGF0aChpdGVtWyJwb3N0Z3JlcyJdWyJwYXRoIl0pCiAgICBpZiAoZHVtcC5pc19zeW1saW5rKCkgb3Igbm90IGR1bXAuaXNfZmlsZSgpCiAgICAgICAgICAgIG9yIG5vdCBkdW1wLnJlc29sdmUoKS5pc19yZWxhdGl2ZV90bygocm9vdC8icG9zdGdyZXMiKS5yZXNvbHZlKCkpCiAgICAgICAgICAgIG9yIGR1bXAubmFtZSAhPSBtYW5pZmVzdF9wYXRoLnN0ZW0gKyAiLmR1bXAiKToKICAgICAgICByYWlzZSBTdG9wKCJ1bnNhZmVfZHVtcCIpCiAgICByYXdfaGFzaCA9IGhhc2hsaWIuc2hhMjU2KCkKICAgIHdpdGggZHVtcC5vcGVuKCJyYiIpIGFzIHN0cmVhbToKICAgICAgICBpZiBzdHJlYW0ucmVhZCg1KSAhPSBiIlBHRE1QIjoKICAgICAgICAgICAgcmFpc2UgU3RvcCgiaW52YWxpZF9hcmNoaXZlIikKICAgICAgICBzdHJlYW0uc2VlaygwKQogICAgICAgIGZvciBjaHVuayBpbiBpdGVyKGxhbWJkYTogc3RyZWFtLnJlYWQoMTAyNCoxMDI0KSwgYiIiKToKICAgICAgICAgICAgcmF3X2hhc2gudXBkYXRlKGNodW5rKQogICAgaWYgKGR1bXAuc3RhdCgpLnN0X3NpemUgPD0gMCBvciBkdW1wLnN0YXQoKS5zdF9zaXplICE9IGl0ZW1bInBvc3RncmVzIl1bImJ5dGVzIl0KICAgICAgICAgICAgb3IgcmF3X2hhc2guaGV4ZGlnZXN0KCkgIT0gaXRlbVsicG9zdGdyZXMiXVsic2hhMjU2Il0pOgogICAgICAgIHJhaXNlIFN0b3AoImJhY2t1cF9pbnRlZ3JpdHlfZmFpbGVkIikKICAgIHJldHVybiBpdGVtLCBkdW1wCgoKZGVmIGRlY29kZWRfbWlncmF0aW9ucygpOgogICAgZmlsZXMgPSB7fQogICAgZm9yIG5hbWUsIGl0ZW0gaW4gTUlHUkFUSU9OUy5pdGVtcygpOgogICAgICAgIHJhdyA9IGJhc2U2NC5iNjRkZWNvZGUoaXRlbVsiYmFzZTY0Il0sIHZhbGlkYXRlPVRydWUpCiAgICAgICAgaWYgaGFzaGxpYi5zaGEyNTYocmF3KS5oZXhkaWdlc3QoKSAhPSBpdGVtWyJzaGEyNTYiXToKICAgICAgICAgICAgcmFpc2UgU3RvcCgibWlncmF0aW9uX2hhc2hfbWlzbWF0Y2giKQogICAgICAgIGZpbGVzW25hbWVdID0gcmF3CiAgICByZXR1cm4gZmlsZXMKCgpkZWYgY2hlY2tfc2NhbGUocmVzdG9yZWQsIG1hbmlmZXN0KToKICAgIGlmIChyZXN0b3JlZFsiZGF0YWJhc2UiXSAhPSAibW9vZGZsb3dfYWdlbnQiIG9yIHJlc3RvcmVkWyJoaXN0b3J5X3Jvd3MiXSAhPSBFWFBFQ1RFRF9ISVNUT1JZX1JPV1MKICAgICAgICAgICAgb3IgcmVzdG9yZWRbImxlZGdlciJdICE9IE9MRF9MRURHRVIgb3IgcmVzdG9yZWRbImV4ZWN1dGlvbl9leGlzdHMiXSBvciByZXN0b3JlZFsiZXZlbnRfc2VxX2V4aXN0cyJdKToKICAgICAgICByYWlzZSBTdG9wKCJyZXN0b3JlZF9zY2FsZV9vcl9zY2hlbWFfbWlzbWF0Y2giKQogICAgaWYgcmVzdG9yZWRbInZlY3RvciJdICE9IG1hbmlmZXN0WyJwb3N0Z3Jlc19yZXN0b3JlIl1bInBndmVjdG9yX3ZlcnNpb24iXToKICAgICAgICByYWlzZSBTdG9wKCJyZXN0b3JlZF92ZWN0b3JfbWlzbWF0Y2giKQogICAgaWYgYW55KHJlc3RvcmVkW3RdICE9IG1hbmlmZXN0WyJwb3N0Z3Jlc19yZXN0b3JlIl1bdF0gZm9yIHQgaW4gQ09SRSk6CiAgICAgICAgcmFpc2UgU3RvcCgicmVzdG9yZWRfY29yZV9jb3VudHNfbWlzbWF0Y2giKQoKCmRlZiBpbnNwZWN0X293bmVyKGtpbmQsIG5hbWUsIHRva2VuKToKICAgIGlmIG5vdCByZS5mdWxsbWF0Y2gociJtZi1jb250ZXh0LXJlaGVhcnNhbC1bMC05YS1mXXsxMn0iLCBuYW1lKToKICAgICAgICByYWlzZSBTdG9wKCJ1bnNhZmVfcmVzb3VyY2VfbmFtZSIpCiAgICBmbXQgPSAie3tqc29uIC5Db25maWcuTGFiZWxzfX0iIGlmIGtpbmQgPT0gImNvbnRhaW5lciIgZWxzZSAie3tqc29uIC5MYWJlbHN9fSIKICAgIGxhYmVscyA9IGpzb24ubG9hZHModGV4dChbImRvY2tlciIsIGtpbmQsICJpbnNwZWN0IiwgIi0tZm9ybWF0IiwgZm10LCBuYW1lXSkpCiAgICBpZiBsYWJlbHMuZ2V0KExBQkVMKSAhPSB0b2tlbjoKICAgICAgICByYWlzZSBTdG9wKCJyZXNvdXJjZV9vd25lcnNoaXBfbWlzbWF0Y2giKQoKCmRlZiBjbGVhbnVwX3Jlc291cmNlcyhuYW1lLCB0b2tlbiwgbWFkZV9jb250YWluZXIsIG1hZGVfdm9sdW1lKToKICAgIGlmIG1hZGVfY29udGFpbmVyOgogICAgICAgIGluc3BlY3Rfb3duZXIoImNvbnRhaW5lciIsIG5hbWUsIHRva2VuKQogICAgICAgIHRleHQoWyJkb2NrZXIiLCAicm0iLCAiLWYiLCBuYW1lXSkKICAgIGlmIG1hZGVfdm9sdW1lOgogICAgICAgIGluc3BlY3Rfb3duZXIoInZvbHVtZSIsIG5hbWUsIHRva2VuKQogICAgICAgIHRleHQoWyJkb2NrZXIiLCAidm9sdW1lIiwgInJtIiwgbmFtZV0pCgoKY2xhc3MgTGFiOgogICAgZGVmIF9faW5pdF9fKHNlbGYsIG5hbWUsIHRva2VuKToKICAgICAgICBzZWxmLm5hbWUsIHNlbGYudG9rZW4gPSBuYW1lLCB0b2tlbgoKICAgIGRlZiBjb21tYW5kKHNlbGYsICphcmdzLCBkYXRhPU5vbmUsIHN0cmVhbT1Ob25lLCBjaGVjaz1UcnVlLCB0aW1lb3V0PTkwKToKICAgICAgICBpbnNwZWN0X293bmVyKCJjb250YWluZXIiLCBzZWxmLm5hbWUsIHNlbGYudG9rZW4pCiAgICAgICAgcmV0dXJuIHJ1bihbImRvY2tlciIsICJleGVjIiwgIi1pIiwgc2VsZi5uYW1lLCAqYXJnc10sCiAgICAgICAgICAgICAgICAgICBkYXRhPWRhdGEsIHN0cmVhbT1zdHJlYW0sIGNoZWNrPWNoZWNrLCB0aW1lb3V0PXRpbWVvdXQpCgogICAgZGVmIHdyaXRlKHNlbGYsIG5hbWUsIHJhdyk6CiAgICAgICAgaWYgbmFtZSBub3QgaW4gKCJzZXJ2aWNlLmNvbmYiLCAiZW1wdHkucGdwYXNzIiwgKk1JR1JBVElPTlMua2V5cygpKToKICAgICAgICAgICAgcmFpc2UgU3RvcCgidW5zYWZlX2xhYl9maWxlIikKICAgICAgICBzZWxmLmNvbW1hbmQoInNoIiwgIi1jIiwgInVtYXNrIDA3NzsgY2F0ID4gIiArIElOTkVSICsgIi8iICsgbmFtZSArICI7IGNobW9kIDQwMCAiICsgSU5ORVIgKyAiLyIgKyBuYW1lLCBkYXRhPXJhdykKCiAgICBkZWYgc3FsKHNlbGYsIHNvdXJjZSwgY2hlY2s9VHJ1ZSk6CiAgICAgICAgaW5zcGVjdF9vd25lcigiY29udGFpbmVyIiwgc2VsZi5uYW1lLCBzZWxmLnRva2VuKQogICAgICAgIHJldHVybiBydW4oWyJkb2NrZXIiLCAiZXhlYyIsICItaSIsICItZSIsICJQR1NFUlZJQ0VGSUxFPSIgKyBJTk5FUiArICIvc2VydmljZS5jb25mIiwKICAgICAgICAgICAgICAgICAgICAiLWUiLCAiUEdQQVNTRklMRT0iICsgSU5ORVIgKyAiL2VtcHR5LnBncGFzcyIsICItZSIsICJQR1BBU1NXT1JEPSIsCiAgICAgICAgICAgICAgICAgICAgIi1lIiwgIlBHT1BUSU9OUz0iLCBzZWxmLm5hbWUsICJwc3FsIiwKICAgICAgICAgICAgICAgICAgICAic2VydmljZT1tb29kZmxvdy1hcHByb3ZlZC1taWdyYXRpb24iLCAiLVgiLCAiLXciLCAiLXEiLCAiLUEiLCAiLXQiLAogICAgICAgICAgICAgICAgICAgICItdiIsICJPTl9FUlJPUl9TVE9QPTEiLCAiLXYiLCAiVkVSQk9TSVRZPXNxbHN0YXRlIiwKICAgICAgICAgICAgICAgICAgICAiLXYiLCAiU0hPV19DT05URVhUPW5ldmVyIiwgIi1QIiwgInBhZ2VyPW9mZiJdLAogICAgICAgICAgICAgICAgICAgZGF0YT1zb3VyY2UuZW5jb2RlKCksIGNoZWNrPWNoZWNrLCB0aW1lb3V0PTE1MCkKCiAgICBkZWYgbWV0YWRhdGEoc2VsZik6CiAgICAgICAgcGFpcnMgPSAiLCAiLmpvaW4oIiciICsgdCArICInLCAoU0VMRUNUIGNvdW50KCopIEZST00gIiArIHQgKyAiKSIgZm9yIHQgaW4gQ09SRSkKICAgICAgICBzcWwgPSAiIiJCRUdJTiBSRUFEIE9OTFk7ClNFVCBMT0NBTCBzdGF0ZW1lbnRfdGltZW91dD0nMTBzJzsKU0VMRUNUIGpzb25fYnVpbGRfb2JqZWN0KAonZGF0YWJhc2UnLCBjdXJyZW50X2RhdGFiYXNlKCksICdkYXRhYmFzZV9ieXRlcycsIHBnX2RhdGFiYXNlX3NpemUoY3VycmVudF9kYXRhYmFzZSgpKSwKJ2hpc3Rvcnlfcm93cycsIChTRUxFQ1QgY291bnQoKikgRlJPTSBhZ2VudF9oaXN0b3J5X2V2ZW50KSwKJ2hpc3RvcnlfYnl0ZXMnLCBwZ190b3RhbF9yZWxhdGlvbl9zaXplKCdhZ2VudF9oaXN0b3J5X2V2ZW50JyksCidsZWRnZXInLCAoU0VMRUNUIGpzb25fYWdnKHZlcnNpb24gT1JERVIgQlkgdmVyc2lvbikgRlJPTSBhZ2VudF9zY2hlbWFfbWlncmF0aW9uKSwKJ2V4ZWN1dGlvbl9leGlzdHMnLCB0b19yZWdjbGFzcygncHVibGljLmFnZW50X2V4ZWN1dGlvbicpIElTIE5PVCBOVUxMLAonZXZlbnRfc2VxX2V4aXN0cycsIEVYSVNUUyhTRUxFQ1QgMSBGUk9NIGluZm9ybWF0aW9uX3NjaGVtYS5jb2x1bW5zIFdIRVJFIHRhYmxlX3NjaGVtYT0ncHVibGljJyBBTkQgdGFibGVfbmFtZT0nYWdlbnRfaGlzdG9yeV9ldmVudCcgQU5EIGNvbHVtbl9uYW1lPSdldmVudF9zZXEnKSwKJ3ZlY3RvcicsIChTRUxFQ1QgZXh0dmVyc2lvbiBGUk9NIHBnX2V4dGVuc2lvbiBXSEVSRSBleHRuYW1lPSd2ZWN0b3InKSwgIiIiICsgcGFpcnMgKyAiIiIpOwpST0xMQkFDSzsKIiIiCiAgICAgICAgcmV0dXJuIGpzb24ubG9hZHMoc2VsZi5zcWwoc3FsKS5zdGRvdXQpCgogICAgZGVmIGRpc2soc2VsZik6CiAgICAgICAgZGF0YV9ieXRlcyA9IHNlbGYuY29tbWFuZCgiZHUiLCAiLXNiIiwgIi92YXIvbGliL3Bvc3RncmVzcWwvZGF0YSIpLnN0ZG91dC5kZWNvZGUoKS5zcGxpdCgpWzBdCiAgICAgICAgd2FsX2J5dGVzID0gc2VsZi5jb21tYW5kKCJkdSIsICItc2IiLCAiL3Zhci9saWIvcG9zdGdyZXNxbC9kYXRhL3BnX3dhbCIpLnN0ZG91dC5kZWNvZGUoKS5zcGxpdCgpWzBdCiAgICAgICAgcmV0dXJuIHsiZGF0YV9kaXJlY3RvcnlfYXBwYXJlbnRfYnl0ZXMiOiBpbnQoZGF0YV9ieXRlcyksICJ3YWxfZGlyZWN0b3J5X2FwcGFyZW50X2J5dGVzIjogaW50KHdhbF9ieXRlcyl9CgoKZGVmIGd1YXJkZWQoc3RhdGVtZW50KToKICAgIHJldHVybiBzdGF0ZW1lbnQgKyAiXG4iICsgciIiIlxpZiA6RVJST1IKUk9MTEJBQ0s7ClxlY2hvIFJFSEVBUlNBTF9UUkFOU0FDVElPTl9ST0xMRURfQkFDSwpccQpcZW5kaWYKIiIiCgoKZGVmIG1pZ3JhdGlvbl9zcWwoKToKICAgIHNxbCA9IHIiIiJcc2V0IE9OX0VSUk9SX1NUT1Agb2ZmClx0aW1pbmcgb24KIiIiCiAgICBzcWwgKz0gZ3VhcmRlZCgiU0VMRUNUIGNsb2NrX3RpbWVzdGFtcCgpIEFTIHR4X3N0YXJ0ZWQgXFxnc2V0IikKICAgIHNxbCArPSBndWFyZGVkKCJCRUdJTjsiKQogICAgc3FsICs9IGd1YXJkZWQoIlNFVCBMT0NBTCBsb2NrX3RpbWVvdXQ9JzVzJzsiKQogICAgc3FsICs9IGd1YXJkZWQoIlNFVCBMT0NBTCBzdGF0ZW1lbnRfdGltZW91dD0nNjBzJzsiKQogICAgc3FsICs9IGd1YXJkZWQoIlNFTEVDVCBwZ19hZHZpc29yeV94YWN0X2xvY2soaGFzaHRleHQoJ21vb2RmbG93X2FnZW50X3NjaGVtYV9taWdyYXRpb25zJykpOyIpCiAgICBzcWwgKz0gZ3VhcmRlZCgiU0VMRUNUIGNsb2NrX3RpbWVzdGFtcCgpIEFTIGxvY2tfd2luZG93X3N0YXJ0ZWQsIHBnX2N1cnJlbnRfd2FsX2luc2VydF9sc24oKSBBUyB3YWxfYmVmb3JlLCBwZ19kYXRhYmFzZV9zaXplKGN1cnJlbnRfZGF0YWJhc2UoKSkgQVMgZGF0YWJhc2VfYmVmb3JlIFxcZ3NldCIpCiAgICBmb3IgbmFtZSBpbiBNSUdSQVRJT05TOgogICAgICAgIHNxbCArPSBndWFyZGVkKCJcXGkgIiArIElOTkVSICsgIi8iICsgbmFtZSkKICAgIHNxbCArPSBndWFyZGVkKCJJTlNFUlQgSU5UTyBhZ2VudF9zY2hlbWFfbWlncmF0aW9uKHZlcnNpb24pIFZBTFVFUyAoJzAwM19jb250ZXh0X2xvb3BfaGlzdG9yeS5zcWwnKSwgKCcwMDRfY29udGludWl0eV9leGVjdXRpb24uc3FsJykgT04gQ09ORkxJQ1QodmVyc2lvbikgRE8gTk9USElORzsiKQogICAgc3FsICs9IGd1YXJkZWQoIlNFTEVDVCBFWElTVFMoU0VMRUNUIDEgRlJPTSBwZ19sb2NrcyBXSEVSRSBwaWQ9cGdfYmFja2VuZF9waWQoKSBBTkQgcmVsYXRpb249J2FnZW50X2hpc3RvcnlfZXZlbnQnOjpyZWdjbGFzcyBBTkQgbW9kZT0nQWNjZXNzRXhjbHVzaXZlTG9jaycgQU5EIGdyYW50ZWQpIEFTIGhpc3RvcnlfbG9ja2VkIFxcZ3NldCIpCiAgICBzcWwgKz0gciIiIlxlY2hvIEhJU1RPUllfQUNDRVNTX0VYQ0xVU0lWRV9IRUxEPTpoaXN0b3J5X2xvY2tlZApcaWYgOmhpc3RvcnlfbG9ja2VkClxlbHNlClJPTExCQUNLOwpcZWNobyBSRUhFQVJTQUxfVFJBTlNBQ1RJT05fUk9MTEVEX0JBQ0sKXHEKXGVuZGlmCiIiIgogICAgc3FsICs9IGd1YXJkZWQoIkNPTU1JVDsiKQogICAgc3FsICs9IGd1YXJkZWQoIlNFTEVDVCBjbG9ja190aW1lc3RhbXAoKSBBUyB0eF9jb21wbGV0ZWQsIHBnX2N1cnJlbnRfd2FsX2luc2VydF9sc24oKSBBUyB3YWxfYWZ0ZXIgXFxnc2V0IikKICAgIHNxbCArPSByIiIiXHNldCBPTl9FUlJPUl9TVE9QIG9uClx0aW1pbmcgb2ZmClNFTEVDVCBqc29uX2J1aWxkX29iamVjdCgKJ3RyYW5zYWN0aW9uX2VsYXBzZWRfbXMnLCByb3VuZChleHRyYWN0KGVwb2NoIEZST00gKDondHhfY29tcGxldGVkJzo6dGltZXN0YW1wdHotOid0eF9zdGFydGVkJzo6dGltZXN0YW1wdHopKSoxMDAwLDMpLAonbG9ja193aW5kb3dfdXBwZXJfYm91bmRfbXMnLCByb3VuZChleHRyYWN0KGVwb2NoIEZST00gKDondHhfY29tcGxldGVkJzo6dGltZXN0YW1wdHotOidsb2NrX3dpbmRvd19zdGFydGVkJzo6dGltZXN0YW1wdHopKSoxMDAwLDMpLAond2FsX2J5dGVzJywgcGdfd2FsX2xzbl9kaWZmKDond2FsX2FmdGVyJzo6cGdfbHNuLCA6J3dhbF9iZWZvcmUnOjpwZ19sc24pLAonZGF0YWJhc2VfYmVmb3JlX2J5dGVzJywgOidkYXRhYmFzZV9iZWZvcmUnOjpiaWdpbnQsCidkYXRhYmFzZV9hZnRlcl9ieXRlcycsIHBnX2RhdGFiYXNlX3NpemUoY3VycmVudF9kYXRhYmFzZSgpKSk7ClxlY2hvIFJFSEVBUlNBTF9UUkFOU0FDVElPTl9DT01NSVRURUQKIiIiCiAgICByZXR1cm4gc3FsCgoKUE9TVENIRUNLX1NRTCA9ICIiIkJFR0lOIFJFQUQgT05MWTsKU0VUIExPQ0FMIHN0YXRlbWVudF90aW1lb3V0PScxMHMnOwpTRUxFQ1QgdmVyc2lvbiBGUk9NIGFnZW50X3NjaGVtYV9taWdyYXRpb24KV0hFUkUgdmVyc2lvbiBJTiAoJzAwM19jb250ZXh0X2xvb3BfaGlzdG9yeS5zcWwnLCcwMDRfY29udGludWl0eV9leGVjdXRpb24uc3FsJyk7ClNFTEVDVCB0b19yZWdjbGFzcygncHVibGljLmFnZW50X2V4ZWN1dGlvbicpOwpTRUxFQ1QgQ09VTlQoKikgQVMgbWlzc2luZ19zZXF1ZW5jZSBGUk9NIGFnZW50X2hpc3RvcnlfZXZlbnQgV0hFUkUgZXZlbnRfc2VxIElTIE5VTEw7ClNFTEVDVCBldmVudF9rZXksIENPVU5UKCopIEZST00gYWdlbnRfaGlzdG9yeV9ldmVudApXSEVSRSBldmVudF9rZXkgSVMgTk9UIE5VTEwgR1JPVVAgQlkgZXZlbnRfa2V5IEhBVklORyBDT1VOVCgqKSA+IDE7ClNFTEVDVCBldmVudF9zZXEsIENPVU5UKCopIEZST00gYWdlbnRfaGlzdG9yeV9ldmVudApHUk9VUCBCWSBldmVudF9zZXEgSEFWSU5HIENPVU5UKCopID4gMTsKU0VMRUNUIHRnbmFtZSwgdGdlbmFibGVkIEZST00gcGdfdHJpZ2dlcgpXSEVSRSB0Z3JlbGlkPSdhZ2VudF9oaXN0b3J5X2V2ZW50Jzo6cmVnY2xhc3MgQU5EIE5PVCB0Z2lzaW50ZXJuYWw7ClNFTEVDVCBqc29uX2J1aWxkX29iamVjdCgKJ21pc3Npbmdfc2VxdWVuY2UnLCAoU0VMRUNUIGNvdW50KCopIEZST00gYWdlbnRfaGlzdG9yeV9ldmVudCBXSEVSRSBldmVudF9zZXEgSVMgTlVMTCksCidkdXBsaWNhdGVfa2V5X2dyb3VwcycsIChTRUxFQ1QgY291bnQoKikgRlJPTSAoU0VMRUNUIGV2ZW50X2tleSBGUk9NIGFnZW50X2hpc3RvcnlfZXZlbnQgV0hFUkUgZXZlbnRfa2V5IElTIE5PVCBOVUxMIEdST1VQIEJZIGV2ZW50X2tleSBIQVZJTkcgY291bnQoKik+MSkgeCksCidkdXBsaWNhdGVfc2VxdWVuY2VfZ3JvdXBzJywgKFNFTEVDVCBjb3VudCgqKSBGUk9NIChTRUxFQ1QgZXZlbnRfc2VxIEZST00gYWdlbnRfaGlzdG9yeV9ldmVudCBHUk9VUCBCWSBldmVudF9zZXEgSEFWSU5HIGNvdW50KCopPjEpIHgpLAonaW1tdXRhYmxlX3RyaWdnZXJfZW5hYmxlZCcsIEVYSVNUUyhTRUxFQ1QgMSBGUk9NIHBnX3RyaWdnZXIgV0hFUkUgdGdyZWxpZD0nYWdlbnRfaGlzdG9yeV9ldmVudCc6OnJlZ2NsYXNzIEFORCB0Z25hbWU9J2FnZW50X2hpc3RvcnlfZXZlbnRfaW1tdXRhYmxlJyBBTkQgdGdlbmFibGVkPSdPJyksCidzZXF1ZW5jZV91c2FnZScsIGhhc19zZXF1ZW5jZV9wcml2aWxlZ2UoY3VycmVudF91c2VyLCBwZ19nZXRfc2VyaWFsX3NlcXVlbmNlKCdhZ2VudF9oaXN0b3J5X2V2ZW50JywnZXZlbnRfc2VxJyksICdVU0FHRScpLAonZXhlY3V0aW9uX3NlbGVjdCcsIGhhc190YWJsZV9wcml2aWxlZ2UoY3VycmVudF91c2VyLCdhZ2VudF9leGVjdXRpb24nLCdTRUxFQ1QnKSwKJ2V4ZWN1dGlvbl9pbnNlcnQnLCBoYXNfdGFibGVfcHJpdmlsZWdlKGN1cnJlbnRfdXNlciwnYWdlbnRfZXhlY3V0aW9uJywnSU5TRVJUJyksCidleGVjdXRpb25fdXBkYXRlJywgaGFzX3RhYmxlX3ByaXZpbGVnZShjdXJyZW50X3VzZXIsJ2FnZW50X2V4ZWN1dGlvbicsJ1VQREFURScpKTsKUk9MTEJBQ0s7CiIiIgoK'), '<approved-migration-support>', 'exec'), _support.__dict__)
sys.modules['rehearsal_runner'] = _support
"""One approved attempt: quiet checks, 003/004 transaction, read-only verification."""
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import rehearsal_runner as r

REHEARSAL = Path('/secure/moodflow/migration-rehearsals/context-loop-20261002-yopxiydv/report.json')
REPORTS = Path('/secure/moodflow/migration-executions')
AI = 'moodflow-production-ai-service-1'

# Only counts are returned. No messages, run identifiers, queries, or blob contents.
# Historical writes are excluded unless they belong to the latest checkpoint.
GATE_QUERY = """WITH latest AS (
 SELECT DISTINCT ON(thread_id,checkpoint_ns) * FROM checkpoints
 ORDER BY thread_id,checkpoint_ns,checkpoint_id DESC
), progress AS (
 SELECT DISTINCT ON(task_id,session_id,body#>>'{payload,run,run_id}')
 body#>'{payload,run}' AS run FROM agent_history_event
 WHERE event_type='run_progress'
 ORDER BY task_id,session_id,body#>>'{payload,run,run_id}',event_timestamp DESC,event_id DESC
), controls AS (
 SELECT c.checkpoint, v.key, v.value, b.type, b.blob,
 CASE WHEN v.key='__start__' THEN '__start__'
 ELSE regexp_replace(v.key, '^.*:', '') END AS node
 FROM latest c CROSS JOIN LATERAL jsonb_each_text(c.checkpoint->'channel_versions') v
 LEFT JOIN checkpoint_blobs b ON b.thread_id=c.thread_id
 AND b.checkpoint_ns=c.checkpoint_ns AND b.channel=v.key AND b.version=v.value
 WHERE v.key LIKE 'branch:%' OR v.key LIKE 'start:%' OR v.key LIKE 'join:%'
 OR v.key IN ('__start__','__pregel_tasks')
)
SELECT json_build_object(
 'database',current_database(),'role',current_user,'checked_at',clock_timestamp(),
 'readonly',current_setting('transaction_read_only'), 'standby',pg_is_in_recovery(),
 'ledger',(SELECT json_agg(version ORDER BY version) FROM agent_schema_migration),
 'history_rows',(SELECT count(*) FROM agent_history_event),
 'database_bytes',pg_database_size(current_database()),
 'execution_exists',to_regclass('public.agent_execution') IS NOT NULL,
 'event_seq_exists',EXISTS(SELECT 1 FROM information_schema.columns
 WHERE table_schema='public' AND table_name='agent_history_event' AND column_name IN ('event_seq','event_key')),
 'immutable_enabled',EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='agent_history_event'::regclass
 AND tgname='agent_history_event_immutable' AND tgenabled='O'),
 'active_sessions',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
 AND pid<>pg_backend_pid() AND backend_type='client backend' AND
 (state IN ('active','idle in transaction','idle in transaction (aborted)') OR state IS NULL)),
 'advisory_locks',(SELECT count(*) FROM pg_locks WHERE locktype='advisory'
 AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid()),
 'active_runs',(SELECT count(*) FROM progress WHERE run->>'status' IN ('running','finalizing')),
 'pending_runs',(SELECT count(*) FROM progress WHERE
 CASE WHEN jsonb_typeof(run->'pending_task_ids')='array'
 THEN jsonb_array_length(run->'pending_task_ids')>0 ELSE true END),
 'unknown_runs',(SELECT count(*) FROM progress WHERE coalesce(run->>'run_id','')=''
 OR coalesce(run->>'status','') NOT IN ('running','finalizing','completed','needs_input','failed')),
 'latest_pending_writes',(SELECT count(*) FROM checkpoint_writes w JOIN latest c
 ON c.thread_id=w.thread_id AND c.checkpoint_ns=w.checkpoint_ns AND c.checkpoint_id=w.checkpoint_id
 WHERE w.channel<>'__no_writes__'),
 'legacy_pending_sends',(SELECT count(*) FROM checkpoint_writes w JOIN latest c
 ON c.thread_id=w.thread_id AND c.checkpoint_ns=w.checkpoint_ns AND c.parent_checkpoint_id=w.checkpoint_id
 WHERE w.channel='__pregel_tasks' AND coalesce((c.checkpoint->>'v')::int,0)<4),
 'pending_controls',(SELECT count(*) FROM controls WHERE type<>'empty' AND
 CASE WHEN key='__pregel_tasks' THEN encode(blob,'hex') NOT IN ('90','c0','5b5d','6e756c6c')
 ELSE coalesce(checkpoint#>>ARRAY['versions_seen',node,key],'')<>value END),
 'unknown_checkpoints',(SELECT count(*) FROM latest WHERE
 coalesce(jsonb_typeof(checkpoint->'channel_versions'),'')<>'object'
 OR coalesce(jsonb_typeof(checkpoint->'versions_seen'),'')<>'object') +
 (SELECT count(*) FROM controls WHERE type IS NULL OR (type<>'empty' AND blob IS NULL)),
 'replication_senders',(SELECT count(*) FROM pg_stat_replication),
 'replication_slots',(SELECT count(*) FROM pg_replication_slots));
"""

NETWORK_PROBE = """import json
from pathlib import Path
listen=active=0
for name in ('tcp','tcp6'):
 for line in Path('/proc/net/'+name).read_text().splitlines()[1:]:
  fields=line.split()
  if int(fields[1].split(':')[1],16)==8001:
   listen+=fields[3]=='0A'
   active+=fields[3]=='01'
print(json.dumps({'ai_listeners':listen,'ai_inbound_established':active}))
"""


def checked_rehearsal(path=REHEARSAL):
    if path.is_symlink() or not path.is_file():
        raise r.Stop('rehearsal_report_missing_or_unsafe')
    item = json.loads(path.read_text(encoding='utf-8'))
    if (item.get('status') != 'passed' or item.get('cleanup') != 'passed'
            or item.get('backup_manifest') != r.MANIFEST.name
            or item.get('image') != r.IMAGE
            or item.get('migration_sha256') != {n:v['sha256'] for n,v in r.MIGRATIONS.items()}
            or item.get('lock_timeout') != '5s' or item.get('statement_timeout') != '60s'):
        raise r.Stop('rehearsal_evidence_mismatch')
    if (item['restored_before']['history_rows'] != r.EXPECTED_HISTORY_ROWS
            or item['restored_before']['ledger'] != r.OLD_LEDGER
            or item['restored_after']['ledger'] != r.NEW_LEDGER
            or not 0 < item['metrics']['lock_window_upper_bound_ms'] < 1000
            or not 0 < item['metrics']['transaction_elapsed_ms'] < 1000):
        raise r.Stop('rehearsal_scale_or_timeout_not_confirmed')
    check_postchecks(item['readonly_postchecks'])
    return item


def check_postchecks(item):
    if (any(item[k] != 0 for k in ('missing_sequence','duplicate_key_groups','duplicate_sequence_groups'))
            or not all(item[k] is True for k in ('immutable_trigger_enabled','sequence_usage',
                                               'execution_select','execution_insert','execution_update'))):
        raise r.Stop('readonly_postcheck_failed')


def check_gate(item):
    if (item['database'] != 'moodflow_agent' or item['role'] != 'moodflow'
            or item['readonly'] != 'on' or item['standby'] is not False
            or item['ledger'] != r.OLD_LEDGER or item['execution_exists']
            or item['event_seq_exists'] or item['immutable_enabled'] is not True):
        raise r.Stop('production_prerequisite_changed_no_migration')
    if (item['history_rows'] != r.EXPECTED_HISTORY_ROWS
            or not 0.9*r.EXPECTED_DATABASE_BYTES <= item['database_bytes'] <= 1.1*r.EXPECTED_DATABASE_BYTES):
        raise r.Stop('production_scale_changed_rehearsal_needed')
    for key in ('active_sessions','advisory_locks','active_runs','pending_runs','unknown_runs',
                'latest_pending_writes','legacy_pending_sends','pending_controls','unknown_checkpoints',
                'replication_senders','replication_slots','ai_inbound_established'):
        if item[key] != 0:
            raise r.Stop('gate_3_not_quiet_'+key+'_no_migration')
    if item['ai_listeners'] < 1:
        raise r.Stop('ai_service_activity_not_observable_no_migration')


class Production:
    def __init__(self, inner):
        self.inner = inner

    def sql(self, source, check=True):
        return r.run(['docker','exec','-i','-e','PGSERVICEFILE='+self.inner+'/service.conf',
                      '-e','PGPASSFILE='+self.inner+'/empty.pgpass','-e','PGPASSWORD=',
                      '-e','PGOPTIONS=',r.PRODUCTION,'timeout','--signal=TERM','--kill-after=5','240',
                      'psql','service=moodflow-approved-migration',
                      '-X','-w','-q','-A','-t','-v','ON_ERROR_STOP=1','-v','VERBOSITY=sqlstate',
                      '-v','SHOW_CONTEXT=never','-P','pager=off'],data=source.encode(),check=check,timeout=260)

    def gate(self):
        item = json.loads(self.sql("BEGIN READ ONLY; SET LOCAL statement_timeout='10s';\n"+GATE_QUERY+'\nROLLBACK;').stdout)
        item.update(json.loads(r.run(['docker','exec','-i',AI,'python','-I','-'],data=NETWORK_PROBE.encode()).stdout))
        return item

    def ledger(self):
        source = """BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object('ledger',(SELECT json_agg(version ORDER BY version) FROM agent_schema_migration),
'execution_exists',to_regclass('public.agent_execution') IS NOT NULL,
'event_seq_exists',EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public'
AND table_name='agent_history_event' AND column_name='event_seq'));
ROLLBACK;
"""
        return json.loads(self.sql(source).stdout)


def guarded(statement):
    return statement + '\n' + r'''\if :ERROR
ROLLBACK;
\echo PRODUCTION_TRANSACTION_ROLLED_BACK
\q
\endif
'''


def migration_sql(inner):
    sql = r'''\set ON_ERROR_STOP off
\timing on
'''
    # BEGIN overrides the profile's default read-only mode only for this transaction.
    for statement in ("SELECT clock_timestamp() AS tx_started \\gset", 'BEGIN READ WRITE;',
                      "SET LOCAL lock_timeout='5s';", "SET LOCAL statement_timeout='60s';",
                      "SELECT pg_advisory_xact_lock(hashtext('moodflow_agent_schema_migrations'));"):
        sql += guarded(statement)
    # Recheck database activity and pending state inside the same transaction.
    gate = GATE_QUERY.replace("'readonly',current_setting('transaction_read_only')", "'readonly','on'")
    sql += guarded(gate.replace('SELECT json_build_object(', 'SELECT json_build_object(',1).rstrip().rstrip(';')+' AS gate_json \\gset')
    sql += r'''\echo TRANSACTION_GATE=:gate_json
'''
    # Evaluate independently in SQL, without passing application contents to psql.
    sql += guarded("SELECT (:'gate_json'::jsonb->>'database'='moodflow_agent' AND "
                   ":'gate_json'::jsonb->>'role'='moodflow' AND "
                   "NOT (:'gate_json'::jsonb->>'standby')::boolean AND "
                   "(:'gate_json'::jsonb->>'history_rows')::bigint=328 AND "
                   "(:'gate_json'::jsonb->'ledger')='[\"001_agent_continuity.sql\",\"002_long_term_memory.sql\"]'::jsonb AND "
                   "NOT (:'gate_json'::jsonb->>'execution_exists')::boolean AND "
                   "NOT (:'gate_json'::jsonb->>'event_seq_exists')::boolean AND "
                   "(:'gate_json'::jsonb->>'immutable_enabled')::boolean AND "
                   "NOT EXISTS(SELECT 1 FROM jsonb_each_text(:'gate_json'::jsonb) "
                   "WHERE key IN ('active_sessions','advisory_locks','active_runs','pending_runs','unknown_runs',"
                   "'latest_pending_writes','legacy_pending_sends','pending_controls','unknown_checkpoints',"
                   "'replication_senders','replication_slots') AND value<>'0')) AS quiet \\gset")
    sql += r'''\if :quiet
\else
ROLLBACK;
\echo PRODUCTION_GATE_3_STOPPED_NO_MIGRATION
\q
\endif
'''
    sql += guarded("SELECT clock_timestamp() AS lock_window_started, pg_current_wal_insert_lsn() AS wal_before \\gset")
    for name in r.MIGRATIONS:
        sql += guarded('\\i '+inner+'/'+name)
    sql += guarded("INSERT INTO agent_schema_migration(version) VALUES ('003_context_loop_history.sql'), ('004_continuity_execution.sql') ON CONFLICT(version) DO NOTHING;")
    sql += guarded('COMMIT;')
    sql += guarded("SELECT clock_timestamp() AS tx_completed, pg_current_wal_insert_lsn() AS wal_after \\gset")
    sql += r'''\set ON_ERROR_STOP on
\timing off
SELECT json_build_object(
'transaction_elapsed_ms',round(extract(epoch FROM (:'tx_completed'::timestamptz-:'tx_started'::timestamptz))*1000,3),
'lock_window_upper_bound_ms',round(extract(epoch FROM (:'tx_completed'::timestamptz-:'lock_window_started'::timestamptz))*1000,3),
'wal_bytes',pg_wal_lsn_diff(:'wal_after'::pg_lsn,:'wal_before'::pg_lsn));
\echo PRODUCTION_TRANSACTION_COMMITTED
'''
    return sql


def postcheck_sql():
    source = r.POSTCHECK_SQL
    for marker,query in (
        ('3.1_LEDGER','SELECT version FROM agent_schema_migration'),
        ('3.2_EXECUTION_TABLE',"SELECT to_regclass('public.agent_execution')"),
        ('3.3_MISSING_SEQUENCE','SELECT COUNT(*) AS missing_sequence'),
        ('3.4_DUPLICATE_EVENT_KEY','SELECT event_key, COUNT(*)'),
        ('3.5_DUPLICATE_EVENT_SEQ','SELECT event_seq, COUNT(*)'),
        ('3.6_IMMUTABLE_TRIGGER','SELECT tgname, tgenabled'),
        ('ADDITIONAL_PERMISSION_AND_INTEGRITY_CHECKS','SELECT json_build_object(')):
        source = source.replace(query,'\\echo '+marker+'\n'+query,1)
    return source


def main():
    if os.geteuid() != 0:
        raise r.Stop('root_terminal_required')
    manifest, _ = r.checked_backup()
    checked_rehearsal()
    files = r.decoded_migrations()
    if r.text(['docker','inspect','--format','{{.Config.Image}}',r.PRODUCTION]) != r.IMAGE:
        raise r.Stop('production_image_changed')
    if REPORTS.is_symlink():
        raise r.Stop('unsafe_report_directory')
    REPORTS.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(REPORTS,0o700)
    folder = Path(tempfile.mkdtemp(prefix='context-loop-20261002-',dir=REPORTS))
    inner = r.text(['docker','exec',r.PRODUCTION,'mktemp','-d','/tmp/mf-approved-migration-XXXXXXXXXXXX'])
    if not re.fullmatch('/tmp/mf-approved-migration-[A-Za-z0-9]{12}',inner):
        raise r.Stop('unsafe_container_temporary_path')
    db = Production(inner)
    report = {'status':'running','started_at_utc':datetime.now(timezone.utc).isoformat(),
              'backup_manifest':r.MANIFEST.name,'rehearsal_report':str(REHEARSAL),
              'backup_completed_at_utc':manifest.get('completed_at_utc'),
              'backup_integrity_rechecked':True,'backup_restore_verified':True,
              'recovery_responsible':'唐艺宁','lock_timeout':'5s','statement_timeout':'60s',
              'migration_sha256':{n:v['sha256'] for n,v in r.MIGRATIONS.items()},
              'production_transaction_attempted':False,'production_commit_confirmed':False,
              'real_write_verification':'skipped_no_dedicated_test_account',
              'gate_3_observations':[],'replication_lag_seconds':None}
    try:
        profile = b'[moodflow-approved-migration]\nhost=/var/run/postgresql\nport=5432\ndbname=moodflow_agent\nuser=moodflow\nconnect_timeout=5\noptions=-c default_transaction_read_only=on\n'
        staged = {'service.conf':profile,'empty.pgpass':b'',**files}
        for name, raw in staged.items():
            r.run(['docker','exec','-i',r.PRODUCTION,'sh','-c','umask 077; cat > '+inner+'/'+name+'; chmod 400 '+inner+'/'+name],data=raw)
            if name in files and r.text(['docker','exec',r.PRODUCTION,'sha256sum',inner+'/'+name]).split()[0] != r.MIGRATIONS[name]['sha256']:
                raise r.Stop('staged_migration_hash_mismatch')
        print('STAGE=gate_3_readonly_quiet_check_10_seconds',flush=True)
        for sample in range(6):
            item = db.gate()
            report['gate_3_observations'].append(item)
            print('GATE3='+json.dumps(item),flush=True)
            check_gate(item)
            if sample < 5:
                time.sleep(2)
        print('STAGE=one_approved_production_transaction',flush=True)
        report['production_transaction_attempted'] = True
        started = time.monotonic()
        result = db.sql(migration_sql(inner),check=False)
        report['migration_wall_seconds'] = round(time.monotonic()-started,6)
        output = result.stdout.decode('utf-8','replace')
        (folder/'migration-output.txt').write_text(output,encoding='utf-8')
        print(output,flush=True)
        report['ledger_after_attempt'] = db.ledger()
        committed = 'PRODUCTION_TRANSACTION_COMMITTED' in output
        report['production_commit_confirmed'] = committed
        if result.returncode or result.stderr or not committed:
            report['rollback_marker_observed'] = 'PRODUCTION_TRANSACTION_ROLLED_BACK' in output
            report['sqlstate_only_errors'] = re.findall(r'ERROR:\s*([A-Z0-9]{5})\b',result.stderr.decode('utf-8','replace'))
            raise r.Stop('transaction_not_verified_inspect_ledger_no_retry')
        if report['ledger_after_attempt']['ledger'] != r.NEW_LEDGER:
            raise r.Stop('committed_ledger_mismatch_no_retry')
        report['metrics'] = json.loads(next(line for line in output.splitlines() if line.startswith('{')))
        print('STAGE=runbook_section_3_readonly_postchecks',flush=True)
        checks = db.sql(postcheck_sql()).stdout.decode()
        (folder/'readonly-postchecks.txt').write_text(checks,encoding='utf-8')
        print('RUNBOOK_SECTION_3_OUTPUT_BEGIN\n'+checks+'RUNBOOK_SECTION_3_OUTPUT_END',flush=True)
        final = json.loads(next(line for line in reversed(checks.splitlines()) if line.startswith('{')))
        check_postchecks(final)
        report['readonly_postchecks'] = final
        report['status'] = 'passed'
    except (r.Stop,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired,KeyboardInterrupt) as exc:
        report['status'] = 'stopped_or_failed'
        report['reason'] = str(exc) if isinstance(exc,r.Stop) else type(exc).__name__
        if report['production_transaction_attempted'] and 'ledger_after_attempt' not in report:
            try:
                report['ledger_after_attempt'] = db.ledger()
            except (r.Stop,OSError,ValueError,subprocess.TimeoutExpired):
                report['ledger_after_attempt'] = 'unconfirmed_do_not_retry'
        print('STOP: '+report['reason'],flush=True)
    finally:
        # Exact files created in this mktemp directory; never recursive deletion.
        try:
            cleaned = r.run(['docker','exec',r.PRODUCTION,'rm','-f',*[inner+'/'+n for n in ('service.conf','empty.pgpass',*files)]],check=False)
            removed = r.run(['docker','exec',r.PRODUCTION,'rmdir',inner],check=False)
            report['temporary_profile_cleanup'] = 'passed' if cleaned.returncode == removed.returncode == 0 else 'needs_review'
        except (r.Stop,OSError,subprocess.TimeoutExpired):
            report['temporary_profile_cleanup'] = 'needs_review'
        report['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
        (folder/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print('REPORT_PATH='+str(folder/'report.json'),flush=True)
        print('PRODUCTION_RESULT='+json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)
        if report['status'] == 'passed':
            print('PRODUCTION_MIGRATION_AND_READONLY_POSTCHECKS_FINISHED; real_write_test_skipped; rollout_not_approved',flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    os.umask(0o077)
    try:
        raise SystemExit(main())
    except (r.Stop,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired):
        print('STOP: production_command_failed_check_report_and_ledger_no_retry')
        raise SystemExit(1)

MF_APPROVED_PRODUCTION
