EXTERN g_fn:QWORD
.code
GetFileVersionInfoA PROC
    jmp QWORD PTR [g_fn + 0]
GetFileVersionInfoA ENDP
GetFileVersionInfoByHandle PROC
    jmp QWORD PTR [g_fn + 8]
GetFileVersionInfoByHandle ENDP
GetFileVersionInfoExA PROC
    jmp QWORD PTR [g_fn + 16]
GetFileVersionInfoExA ENDP
GetFileVersionInfoExW PROC
    jmp QWORD PTR [g_fn + 24]
GetFileVersionInfoExW ENDP
GetFileVersionInfoSizeA PROC
    jmp QWORD PTR [g_fn + 32]
GetFileVersionInfoSizeA ENDP
GetFileVersionInfoSizeExA PROC
    jmp QWORD PTR [g_fn + 40]
GetFileVersionInfoSizeExA ENDP
GetFileVersionInfoSizeExW PROC
    jmp QWORD PTR [g_fn + 48]
GetFileVersionInfoSizeExW ENDP
GetFileVersionInfoSizeW PROC
    jmp QWORD PTR [g_fn + 56]
GetFileVersionInfoSizeW ENDP
GetFileVersionInfoW PROC
    jmp QWORD PTR [g_fn + 64]
GetFileVersionInfoW ENDP
VerFindFileA PROC
    jmp QWORD PTR [g_fn + 72]
VerFindFileA ENDP
VerFindFileW PROC
    jmp QWORD PTR [g_fn + 80]
VerFindFileW ENDP
VerInstallFileA PROC
    jmp QWORD PTR [g_fn + 88]
VerInstallFileA ENDP
VerInstallFileW PROC
    jmp QWORD PTR [g_fn + 96]
VerInstallFileW ENDP
VerLanguageNameA PROC
    jmp QWORD PTR [g_fn + 104]
VerLanguageNameA ENDP
VerLanguageNameW PROC
    jmp QWORD PTR [g_fn + 112]
VerLanguageNameW ENDP
VerQueryValueA PROC
    jmp QWORD PTR [g_fn + 120]
VerQueryValueA ENDP
VerQueryValueW PROC
    jmp QWORD PTR [g_fn + 128]
VerQueryValueW ENDP
END
