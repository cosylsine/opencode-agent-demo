FUNCTION_BLOCK FB_Ratio
VAR_INPUT
    Enable : BOOL;
    Numerator : REAL;
    Denominator : REAL;
END_VAR
VAR_OUTPUT
    Ratio : REAL;
    Valid : BOOL;
END_VAR

IF Enable THEN
    Ratio := Numerator / Denominator;
    Valid := TRUE;
END_IF;
END_FUNCTION_BLOCK
