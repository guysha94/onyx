import React from "react";
import { Formik, useFormikContext } from "formik";
import { render, screen, setupUser } from "@tests/setup/test-utils";
import type { ModelConfiguration } from "@/lib/languageModels/types";
import { ModelSelectionField } from "@/sections/modals/languageModels/shared";
import type { BaseLLMFormValues } from "@/sections/modals/languageModels/utils";

jest.mock("@/refresh-components/inputs/InputSelect", () => {
  function MockInputSelect({
    value,
    onValueChange,
    children,
  }: {
    value?: string;
    onValueChange?: (value: string) => void;
    children?: React.ReactNode;
  }) {
    return (
      <div data-testid="vision-select" data-value={value}>
        <button type="button" onClick={() => onValueChange?.("text-image")}>
          Set Text & Image
        </button>
        {children}
      </div>
    );
  }
  MockInputSelect.Trigger = function Trigger() {
    return null;
  };
  MockInputSelect.Content = function Content({
    children,
  }: {
    children?: React.ReactNode;
  }) {
    return <>{children}</>;
  };
  MockInputSelect.Item = function Item({
    children,
  }: {
    children?: React.ReactNode;
  }) {
    return <>{children}</>;
  };
  return MockInputSelect;
});

function makeModel(
  overrides: Partial<ModelConfiguration> & Pick<ModelConfiguration, "name">
): ModelConfiguration {
  return {
    is_visible: true,
    max_input_tokens: null,
    supports_image_input: false,
    supports_reasoning: false,
    effectiveDisplayName: overrides.name,
    ...overrides,
  };
}

function ModelConfigsReadout() {
  const { values } = useFormikContext<BaseLLMFormValues>();
  return (
    <pre data-testid="configs">
      {JSON.stringify(
        values.model_configurations.map((m) => ({
          name: m.name,
          supports_image_input: m.supports_image_input,
        }))
      )}
    </pre>
  );
}

function renderField(options: {
  allowVisionOverride?: boolean;
  models?: ModelConfiguration[];
}) {
  const models = options.models ?? [
    makeModel({ name: "qwen2.5-vl-7b", display_name: "Qwen2.5-VL-7B" }),
  ];

  const initialValues: BaseLLMFormValues = {
    is_public: true,
    is_auto_mode: false,
    groups: [],
    personas: [],
    model_configurations: models,
    test_model_name: models[0]?.name,
  };

  return render(
    <Formik initialValues={initialValues} onSubmit={() => {}}>
      <>
        <ModelSelectionField
          shouldShowAutoUpdateToggle={false}
          allowVisionOverride={options.allowVisionOverride}
        />
        <ModelConfigsReadout />
      </>
    </Formik>
  );
}

describe("ModelSelectionField allowVisionOverride", () => {
  it("does not show the vision input-type control when allowVisionOverride is off", () => {
    renderField({ allowVisionOverride: false });
    expect(screen.queryByTestId("vision-select")).not.toBeInTheDocument();
  });

  it("toggles supports_image_input and shows the vision marker", async () => {
    const user = setupUser();
    renderField({ allowVisionOverride: true });

    expect(screen.getByTestId("configs")).toHaveTextContent(
      '"supports_image_input":false'
    );
    expect(screen.getByText("Input type")).toBeInTheDocument();
    expect(screen.getByText("Text Only")).toBeInTheDocument();
    expect(screen.getByTestId("vision-select")).toHaveAttribute(
      "data-value",
      "text-only"
    );
    expect(screen.queryByTitle("Vision")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Set Text & Image" }));

    expect(screen.getByTestId("configs")).toHaveTextContent(
      '"supports_image_input":true'
    );
    expect(screen.getByText("Text & Image")).toBeInTheDocument();
    expect(screen.getByTestId("vision-select")).toHaveAttribute(
      "data-value",
      "text-image"
    );
    expect(screen.getByTitle("Vision")).toBeInTheDocument();
  });
});
