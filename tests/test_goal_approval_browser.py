"""Actual card/permission controls over labelled synthetic HTTP responses.

The real approval authority is tested separately by test_goal_final_audit; this case
proves the browser reviews the same action and resumes its goal rather than resending chat.
"""
import json

from tests.test_goal_card_browser import _boot, _goal_view, _GoalServer, browser, page


def test_goal_card_reviews_approval_and_resumes_without_recreating_goal(page):
    class Server(_GoalServer):
        def __init__(self):
            super().__init__()
            self.flow = []
            self.approved = False

        def route(self, route):
            request = route.request
            if request.method == 'POST' and request.url.endswith('/api/mode'):
                body = json.loads(request.post_data)
                if body.get('op') != 'resolve_approval':
                    return super().route(route)
                assert body['approval_id'] == 'preview-1'
                assert body['decision'] == 'allow' and body['scope'] == 'once'
                self.flow.append('resolve')
                self.approved = True
                route.fulfill(status=200, content_type='application/json', body=json.dumps({'ok':True, 'approval':{'status':'approved', 'scope':'once'}}))
                return
            if request.url.endswith('/api/goal/retry'):
                body = json.loads(request.post_data)
                self.flow.append('retry')
                view = self.state_views[body['session_id']]
                assert body['goal_id'] == view['goal_id']
                if not self.approved:
                    result = {'ok':False, 'goal':view, 'approval':{'approval_id':'preview-1', 'task_id':'t3',
                        'action':'Write reviewed.txt', 'intent':'workspace.write_file', 'scope_options':['once'],
                        'affected_resources':['reviewed.txt'], 'diff_preview':'+ reviewed bytes'}}
                    route.fulfill(status=409, content_type='application/json', body=json.dumps(result))
                    return
                view['state'] = 'completed'
                view['completed'] = view['total']
                for step in view['steps']:
                    step['state'] = 'completed'
                route.fulfill(status=200, content_type='application/json', body=json.dumps({'ok':True, 'goal':view}))
                return
            super().route(route)

    server = Server()
    _boot(page, server)
    page.wait_for_selector('#input')
    session = page.evaluate('window.VoolPageActions.displayedChat()')
    server.state_views[session] = _goal_view(session, state='waiting_approval')
    page.evaluate('window.VoolGoalCard.refresh()')
    page.wait_for_selector('#voolGoalCard:not(.vgc-hidden)')
    page.click('#vgcHead')
    page.get_by_role('button', name='Resume step', exact=True).click()
    page.wait_for_selector('#permBar:not([hidden])')
    assert 'reviewed.txt' in page.locator('#permBar').inner_text()
    page.click('#permOnce')
    page.wait_for_function("document.getElementById('vgcState').textContent.trim() === 'Completed'")
    assert server.flow == ['retry', 'resolve', 'retry']
    assert server.chat_bodies == [], 'approval must not recreate the goal through a chat resend'
