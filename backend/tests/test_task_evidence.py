from app.service import task_evidence


def test_same_project_files_from_other_task_are_not_proposal_evidence():
    class Connection:
        def execute(self,sql,args):
            assert args==('project','requirements')
            return [{'id':'owned','mapping':[]},{'id':'other-task','mapping':[]}]
    state=task_evidence(Connection(),{'id':'project','account_id':'123'},
        {'file_ids':['owned'],'messages':[{'id':'m','role':'user','content':'facts'}]})
    assert [d['id'] for d in state['documents']]==['owned']
    assert state['project_account_id']=='123' and state['messages'][0]['id']=='m'
