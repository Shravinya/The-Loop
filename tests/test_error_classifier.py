from recovery.error_classifier import ErrorClassifier, ErrorType
from shared.schemas import ToolError


def test_404_is_never_classified_as_retry():
    classifier = ErrorClassifier()
    error = ToolError(error_type="404", message="missing")
    assert classifier.classify(error) == ErrorType.PERMANENT_NOT_FOUND

