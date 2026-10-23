PROGRAM PLC_PRG
VAR
    bStart      : BOOL;
    bStop       : BOOL;
    rFlow       : REAL;
    bPump       : BOOL;
    bMixer      : BOOL;
    bDischarge  : BOOL;
    eStep       : INT := 0;
    bStartEdge  : BOOL;
    bStartOld   : BOOL;
    rTotal      : REAL;
    rBatchSize  : REAL := 50.0;
    tMix        : TON;
    tOut        : TON;
    aTemp       : ARRAY[1..6] OF REAL;
    rTempAvg    : REAL;
    i           : INT;
    iBatchNo    : INT;
END_VAR

bStartOld  := bStart;
bStartEdge := bStart AND NOT bStartOld;

tMix(IN := (eStep = 2), PT := T#30S);

FOR i := 1 TO 6 DO
    rTempAvg := 0.0;
    rTempAvg := rTempAvg + aTemp[i];
END_FOR
rTempAvg := rTempAvg / 6.0;

CASE eStep OF
    0:
        bPump := FALSE;
        bMixer := FALSE;
        bDischarge := FALSE;
        rTotal := 0.0;
        IF bStartEdge AND (rTempAvg > 10.0) THEN
            eStep := 1;
        END_IF

    1:
        bPump := TRUE;
        rTotal := rTotal + rFlow / 60.0;
        IF rTotal >= rBatchSize THEN
            bPump := FALSE;
            eStep := 2;
        END_IF

    2:
        bMixer := TRUE;
        IF tMix.Q THEN
            bMixer := FALSE;
            eStep := 3;
        END_IF

    3:
        bDischarge := TRUE;
        tOut(IN := bDischarge, PT := T#20S);
        IF tOut.Q THEN
            bDischarge := FALSE;
            iBatchNo := iBatchNo + 1;
            eStep := 0;
        END_IF
END_CASE

IF bStop THEN
    eStep := 0;
END_IF
