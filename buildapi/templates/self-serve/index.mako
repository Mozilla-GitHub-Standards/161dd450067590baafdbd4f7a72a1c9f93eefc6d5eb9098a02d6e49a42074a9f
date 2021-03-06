<%inherit file="/base.mako" />
<%!
import textwrap
def f(text):
    return textwrap.dedent(text).strip().replace("\n", "<br/>\n")

%>
<%def name="title()">RelEng Self-Serve API</%def>
<%def name="header()">
<script type="text/javascript">
$(document).ready(function()
{
    // Fill in the branch table
    var table = $('#branches');
    var baselink = '${h.url('selfserve_home')}';
    $.getJSON('${h.url('branches')}', function(data) {
        var branches = [];
        $.each(data, function(index, value) {
            branches.push(index);
        });
        branches.sort();
        $.each(branches, function(index, value) {
            var link = baselink + '/' + value;
            var txt = '<a href="' + link + '">' + link + '</a>';
            table.append('<tr><td>' + txt + '</td></tr>');
        });
    });
})
</script>
</%def>
<%def name="body()">\
<h1>Welcome to the RelEng Self-Serve API</h1>

Available branches:<br/>
<table id="branches"></table>
Click to view builds on that branch<br/>

<p>
<a href="${h.url('jobs')}">self-serve request history</a>
</p>

<h1>API Documentation</h1>
<pre>${textwrap.dedent(c.main_docstring)|n}</pre>
<table>
<tr>
<th>HTTP method</th><th>Path</th><th>Docs</th>
</tr>
% for routepath, method, docstring in c.routes:
<tr><td valign="top">${method}</td><td valign="top">${routepath}</td><td>${docstring|f}</td></tr>
% endfor
</table>
</%def>
