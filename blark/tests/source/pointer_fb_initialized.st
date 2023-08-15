FUNCTION dummyFunction : BOOL
VAR_INPUT
     pt_SomethingNeat : POINTER TO struct_SomethingNeat;
     pt_CrossTaskWriter : POINTER TO fb_crossTaskWrite(ID := gvl_mutexId.MUTEX_ONE, sizeOfStruct := SIZEOF(DINT));
     pt_CrossTaskWriter : POINTER TO fb_crossTaskWrite(ID := gvl_mutexId.MUTEX_TWO, sizeOfStruct := SIZEOF(quality_t));
END_VAR
END_FUNCTION
