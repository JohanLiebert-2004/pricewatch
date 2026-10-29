BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
DO $$
DECLARE v_token text; v_secret text; v_status text; v_ok boolean;
BEGIN
 SET LOCAL ROLE anon;
 v_token:=create_keyword_alert('shopping-qa@example.invalid','unique shopping qa','case',ARRAY['kmart','bigw'],10,50,20);
 ASSERT keyword_alert_status(v_token)='pending';
 ASSERT NOT confirm_keyword_alert(v_token), 'management token must not activate';
 BEGIN
  PERFORM email FROM keyword_alerts;
  RAISE EXCEPTION 'anon read allowed';
 EXCEPTION WHEN insufficient_privilege THEN NULL;
 END;
 BEGIN
  PERFORM create_keyword_alert('shopping-qa@example.invalid','unique shopping qa','case',ARRAY['kmart'],10,'NaN'::numeric,20);
  RAISE EXCEPTION 'invalid price accepted';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM='invalid price accepted' THEN RAISE; END IF;
 END;
 BEGIN
  PERFORM create_keyword_alert('shopping-qa@example.invalid','unique shopping qa','case',ARRAY['kmart','bigw'],10,50,20);
  RAISE EXCEPTION 'duplicate accepted';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM='duplicate accepted' THEN RAISE; END IF;
 END;
 RESET ROLE;
 SELECT confirm_token INTO v_secret FROM keyword_alerts WHERE token=v_token;
 ASSERT v_secret<>v_token;
 SET LOCAL ROLE anon;
 ASSERT confirm_keyword_alert(v_secret);
 ASSERT keyword_alert_status(v_token)='active';
 ASSERT cancel_keyword_alert(v_token);
 ASSERT keyword_alert_status(v_token)='cancelled';
 ASSERT NOT confirm_keyword_alert(v_secret);
 RESET ROLE;
 RAISE NOTICE 'PASS: anonymous RPC lifecycle, separate secrets, private-table denial, validation and duplicate rejection';
END $$;
ROLLBACK;
