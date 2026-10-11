import json
import os
from types import SimpleNamespace
import unittest
from unittest import mock
import core
import db
import public_booking as pb
import public_assistant as assistant

DRAFT = {'cargo':'Boxes','weight_kg':100,'package_count':1,
 'package_length_cm':50,'package_width_cm':40,'package_height_cm':30,
 'origin_island':'Luzon','dest_island':'Luzon','origin_city':'Imus','dest_city':'Manila','km':30}

class AssistantTests(unittest.TestCase):
 def setUp(self):
  self.c=db.connect(':memory:')
  self.env=mock.patch.dict('os.environ',{'LIFTHAUL_ASSISTANT_LIVE':'0'});self.env.start()
 def tearDown(self):
  self.env.stop();self.c.close()
 def ask(self,body):
  return assistant.respond(self.c,assistant.prepare(body))
 def test_guided_mode(self):
  self.assertEqual(self.ask({'message':'hello'})['mode'],'guided')
 def test_filipino_intents(self):
  for text,intent in [('Magkano ang padala?','pricing'),('Nasaan na?','tracking'),('Gusto kong sumali','provider'),('Paano ang bayad?','payments')]:
   self.assertEqual(self.ask({'message':text})['intent'],intent)
 def test_missing_fields(self):
  r=self.ask({'intent':'booking'});self.assertIn('weight_kg',r['missing_fields']);self.assertNotIn('recommendation',r)
 def test_canonical_matching(self):
  r=self.ask({'intent':'booking','shipment':DRAFT})
  self.assertEqual(r['recommendation']['options'],pb.recommend_vehicles(self.c,DRAFT)['options'])
 def test_canonical_quote(self):
  r=self.ask({'intent':'pricing','shipment':DRAFT})
  self.assertEqual(r['estimate']['quote_breakdown'],pb.preview_estimate(self.c,DRAFT)['quote_breakdown'])
 def test_no_invented_distance(self):
  draft=dict(DRAFT);draft.pop('km');self.assertNotIn('estimate',self.ask({'intent':'pricing','shipment':draft}))
 def test_tracking_requires_token(self):
  with self.assertRaises(core.NotFoundError):self.ask({'intent':'tracking','tracking_token':'LH-123'})
 def test_tracking_projection(self):
  booking=pb.submit(self.c,{'contact_name':'Test','contact_phone':'09171234567','origin_island':'Luzon','dest_island':'Luzon','vehicle':'6w','km':45})
  r=self.ask({'intent':'tracking','tracking_token':booking['tracking_token']})
  self.assertEqual(set(r['tracking']),{'ref','customer_status','quotation_status','vehicle_class'})
  self.assertNotIn(booking['tracking_token'],json.dumps(r))
 def test_no_writes(self):
  before=self.c.execute('SELECT COUNT(*) n FROM mkt_bookings').fetchone()['n']
  self.assertFalse(self.ask({'intent':'booking','shipment':DRAFT})['booking_created'])
  self.assertEqual(before,self.c.execute('SELECT COUNT(*) n FROM mkt_bookings').fetchone()['n'])
 def test_financial_actions_denied(self):
  for action in ('release_payment','approve_refund','dispatch_vehicle'):
   with self.assertRaises(core.ValidationError):self.ask({'intent':action})
 def test_invalid_numbers(self):
  for value in ('NaN','Infinity',-1,True,350001):
   with self.assertRaises(core.ValidationError):self.ask({'shipment':{'weight_kg':value}})
 def test_invalid_structure(self):
  for body in ([],{'message':[]},{'message':'x'*1501},{'shipment':[]}):
   with self.assertRaises(core.ValidationError):self.ask(body)
 def test_untrusted_fields(self):
  self.assertEqual(assistant.prepare({'shipment':{'tenant_id':123,'contact_phone':'123','quote_amount':1,'password':'secret','weight_kg':100}})['shipment'],{'weight_kg':100})
 def test_ai_extraction(self):
  with mock.patch.object(assistant,'_live_extract',return_value={'intent':'booking','shipment':DRAFT}):
   r=self.ask({'message':'ship boxes'});self.assertEqual(r['mode'],'ai');self.assertIn('recommendation',r)
 def test_buttons_no_ai(self):
  with mock.patch.object(assistant,'_live_extract') as call:
   self.ask({'intent':'tracking','message':'track'});call.assert_not_called()
 def test_redaction(self):
  s=assistant._redact('token=abc pbk_private sk-secret marc@example.com 09171234567')
  for secret in ('abc','pbk_private','sk-secret','marc@example.com','09171234567'):self.assertNotIn(secret,s)
 def test_provider_failure(self):
  with mock.patch.dict('os.environ',{'LIFTHAUL_ASSISTANT_LIVE':'1','OPENAI_API_KEY':'test-key','LIFTHAUL_ASSISTANT_MODEL':'test-model'}),mock.patch.object(assistant.urllib.request,'urlopen',side_effect=TimeoutError):
   self.assertEqual(self.ask({'message':'book shipment'})['mode'],'guided')
 def test_live_contract(self):
  output={'intent':'booking','shipment':{k:DRAFT.get(k) for k in assistant.FIELDS}}
  response=mock.MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(output)}]}]}).encode()
  with mock.patch.dict('os.environ',{'LIFTHAUL_ASSISTANT_LIVE':'1','OPENAI_API_KEY':'test-key','LIFTHAUL_ASSISTANT_MODEL':'test-model'}),mock.patch.object(assistant.urllib.request,'urlopen',return_value=response) as call:
   r=self.ask({'message':'ship boxes'});data=json.loads(call.call_args.args[0].data)
   self.assertFalse(data['store']);self.assertTrue(data['text']['format']['strict']);self.assertEqual(r['mode'],'ai')
 def test_budget_stop(self):
  with mock.patch.dict('os.environ',{'LIFTHAUL_ASSISTANT_LIVE':'1','OPENAI_API_KEY':'test','LIFTHAUL_ASSISTANT_MODEL':'test','LIFTHAUL_ASSISTANT_CALL_CAP':'0'}),mock.patch.object(assistant.urllib.request,'urlopen') as call:
   self.assertEqual(self.ask({'message':'book shipment'})['mode'],'guided');call.assert_not_called()
 def test_bad_model_output(self):
  response=mock.MagicMock();response.__enter__.return_value.read.return_value=b'{"status":"completed","output":[]}'
  with mock.patch.dict('os.environ',{'LIFTHAUL_ASSISTANT_LIVE':'1','OPENAI_API_KEY':'test','LIFTHAUL_ASSISTANT_MODEL':'test'}),mock.patch.object(assistant.urllib.request,'urlopen',return_value=response):
   self.assertEqual(self.ask({'message':'book shipment'})['mode'],'guided')

class AssistantHttpTests(unittest.TestCase):
 def test_http_contract_and_validation(self):
  import threading
  import urllib.request
  import urllib.error
  from http.server import ThreadingHTTPServer
  import server
  connection=db.connect(':memory:')
  lock=threading.RLock()
  def extract(message):
   self.assertFalse(lock._is_owned(), 'provider must run outside database lock')
   return None
  with mock.patch.object(server,'_conn',connection), mock.patch.object(server,'_POOL',None), mock.patch.object(server,'_DB_LOCK',lock), mock.patch.object(assistant,'_live_extract',side_effect=extract):
   http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
   thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
   url='http://127.0.0.1:'+str(http.server_port)+'/public/assistant/message'
   def request(body):
    return urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=5)
   try:
    with request({'message':'book shipment'}) as response:
     result=json.load(response)['data'];self.assertEqual(result['mode'],'guided');self.assertFalse(result['booking_created'])
    with self.assertRaises(urllib.error.HTTPError) as error:request([])
    self.assertEqual(error.exception.code,422)
    error.exception.close()
   finally:
    http.shutdown();http.server_close();thread.join();connection.close()

 def test_assistant_has_an_independent_rate_limit_bucket(self):
  import server
  server._PUBLIC_HITS.clear();server._ASSISTANT_HITS.clear()
  with mock.patch.object(server,'_PUBLIC_RATE_MAX',1),mock.patch.object(server,'_ASSISTANT_RATE_MAX',1):
   self.assertTrue(server._assistant_rate_ok('203.0.113.10'))
   self.assertFalse(server._assistant_rate_ok('203.0.113.10'))
   self.assertTrue(server._public_rate_ok('203.0.113.10'))

 def test_proxy_address_is_used_only_when_explicitly_trusted(self):
  import server
  handler=SimpleNamespace(client_address=('10.0.0.5',1234),headers={'X-Forwarded-For':'203.0.113.7, 10.0.0.5'})
  with mock.patch.dict(os.environ,{'LIFTHAUL_TRUST_PROXY':'0'}):
   self.assertEqual(server._client_ip(handler),'10.0.0.5')
  with mock.patch.dict(os.environ,{'LIFTHAUL_TRUST_PROXY':'1'}):
   self.assertEqual(server._client_ip(handler),'203.0.113.7')
  handler.headers['X-Forwarded-For']='not-an-ip'
  with mock.patch.dict(os.environ,{'LIFTHAUL_TRUST_PROXY':'1'}):
   self.assertEqual(server._client_ip(handler),'10.0.0.5')

if __name__=='__main__':unittest.main()

