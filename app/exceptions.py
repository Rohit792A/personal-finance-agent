""" Exceptions.py (Custom Error Handling)

In a professional system, we don't just catch generic Exception. We define our own domain-specific errors so our Orchestrator knows exactly why something failed.

"""

class AgentError(Exception):
    """Base exception for the personal finance agent."""
    pass

class ExtractionError(AgentError):
    """Raised when Gemini fails to extract or parse data."""
    pass

class PasswordRequiredError(ExtractionError):
    """A PDF needs a password to open and none that was tried opens it (DEC-060). Gemini rejects
    such files with 400 INVALID_ARGUMENT, so they are stopped before any AI call."""
    pass

class DatabaseError (AgentError):
    """Raised when a database operation fails."""
    pass