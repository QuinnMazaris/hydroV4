# Walkthrough - Backend Crash Fix

## Issue
The application was failing to host correctly because the backend service was crashing on startup.
The logs showed repeated `ECONNREFUSED` errors from the frontend trying to connect to the backend on port 8001.
Investigation revealed the backend was crashing with:
`sqlalchemy.exc.InvalidRequestError: Attribute name 'metadata' is reserved when using the Declarative API.`

This was caused by a column named `metadata` in the `ConversationMessage` model in `backend/models.py`.

## Changes
1.  **Renamed Attribute**: Renamed `metadata` to `message_metadata` in `backend/models.py` for the `ConversationMessage` class.
    ```python
    # Before
    metadata = Column(JSON, nullable=True)
    
    # After
    message_metadata = Column(JSON, nullable=True)
    ```
2.  **Updated Pydantic Models**: Updated `ConversationMessageBase` and related models to use `message_metadata`.
3.  **Updated Services**: Updated `backend/services/agent_history.py` to use the new attribute name.
4.  **Rebuilt Container**: Rebuilt the `hydro-app` docker container to apply the code changes.

## Verification
1.  **Backend Startup**: Verified via `docker logs` that the backend started successfully.
2.  **API Health**: Verified via `curl` that the API is reachable through the frontend proxy.
    ```bash
    curl http://localhost:3001/api/health
    ```
    Output:
    ```json
    {"status":"healthy","mqtt_connected":true,"timestamp":"..."}
    ```

The application should now be accessible at `http://10.0.0.77:3001`.
