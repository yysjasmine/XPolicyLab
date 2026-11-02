import json
import unittest
from importlib import import_module
_module=import_module("XPolicyLab.policy.SenseNova-RoboRSI.cci_protocol")
request_to_cci,response_to_cli,sse=_module.request_to_cci,_module.response_to_cli,_module.sse

class ProtocolTests(unittest.TestCase):

    def test_history_image_and_tools_survive(self):
        request = {'model': 'test', 'instructions': 'core', 'input': [{'type': 'additional_tools', 'tools': [{'type': 'namespace', 'name': 'functions', 'tools': [{'type': 'custom', 'name': 'exec', 'description': 'execute'}]}]}, {'role': 'developer', 'content': [{'type': 'input_text', 'text': 'skills'}]}, {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'call1', 'input': 'text(1)'}, {'type': 'custom_tool_call_output', 'call_id': 'call1', 'output': [{'type': 'input_image', 'image_url': 'data:image/png;base64,YQ=='}]}]}
        result = request_to_cci(request)
        self.assertEqual(result['instructions'], 'core\n\nskills')
        self.assertEqual(result['tools'][0]['name'], 'functions.exec')
        self.assertEqual(json.loads(result['input'][0]['arguments']), {'_raw': 'text(1)'})
        self.assertEqual(result['input'][1]['call_id'], 'call1')
        self.assertEqual(result['input'][1]['output'][0]['image_url'], 'data:image/png;base64,YQ==')
        self.assertEqual(result['input'][1]['output'][0]['detail'], 'auto')
        self.assertNotIn('detail', request['input'][3]['output'][0])

    def test_exact_program_and_opaque_ciphertext(self):
        response = {'id': 'r1', 'status': 'completed', 'output': [{'type': 'reasoning', 'id': 'rs1', 'summary': [], 'encrypted_content': 'opaque'}, {'type': 'function_call', 'id': 'fc1', 'call_id': 'call1', 'name': 'functions.exec', 'arguments': json.dumps({'_raw': 'text("✓\\n");'})}]}
        result = response_to_cli(response)
        self.assertEqual(result['output'][1]['input'], 'text("✓\\n");')
        self.assertEqual(result['output'][1]['namespace'], 'functions')
        self.assertEqual(result['output'][0]['encrypted_content'], 'opaque')
        events = [json.loads(x[6:]) for x in sse(result).decode().splitlines() if x.startswith('data: ')]
        self.assertEqual(events[-1]['type'], 'response.completed')
        self.assertEqual(events[-1]['response'], result)
        self.assertEqual([e['sequence_number'] for e in events], list(range(len(events))))
