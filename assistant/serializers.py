from rest_framework import serializers

from .service import QuestionError, validate_question


class QuestionField(serializers.Field):
    """Normalises whitespace and enforces the same rules as `service.validate_question`."""

    default_error_messages = {"required": "'question' must be a string.", "null": "'question' must be a string."}

    def to_internal_value(self, data):
        try:
            return validate_question(data)
        except QuestionError as exc:
            raise serializers.ValidationError(str(exc))

    def to_representation(self, value):
        return value


class AskSerializer(serializers.Serializer):
    question = QuestionField()
