"""Print counts from a pytest junit-xml file."""
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
suite = root[0] if root.tag == "testsuites" else root
print(
    f"tests={suite.get('tests')} failures={suite.get('failures')} "
    f"errors={suite.get('errors')} skipped={suite.get('skipped')}"
)
for case in suite.iter("testcase"):
    if case.find("failure") is not None or case.find("error") is not None:
        print("FAIL", f"{case.get('classname')}::{case.get('name')}")
