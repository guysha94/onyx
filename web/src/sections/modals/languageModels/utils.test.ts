import type { ModelConfiguration } from "@/lib/languageModels/types";
import { mergeFetchedModelConfigurations } from "@/sections/modals/languageModels/utils";

function makeModel(
  overrides: Partial<ModelConfiguration> & Pick<ModelConfiguration, "name">
): ModelConfiguration {
  return {
    is_visible: false,
    max_input_tokens: null,
    supports_image_input: false,
    supports_reasoning: false,
    effectiveDisplayName: overrides.name,
    ...overrides,
  };
}

describe("mergeFetchedModelConfigurations", () => {
  it("returns fetched list as-is when existing is empty", () => {
    const fetched = [
      makeModel({ name: "a", is_visible: true, supports_image_input: true }),
    ];
    expect(mergeFetchedModelConfigurations(fetched, [])).toBe(fetched);
  });

  it("preserves is_visible and supports_image_input for existing models", () => {
    const existing = [
      makeModel({
        name: "vision-model",
        is_visible: true,
        supports_image_input: true,
      }),
      makeModel({
        name: "text-model",
        is_visible: false,
        supports_image_input: false,
      }),
    ];
    const fetched = [
      makeModel({
        name: "vision-model",
        is_visible: false,
        supports_image_input: false,
        display_name: "Vision (refetched)",
      }),
      makeModel({
        name: "text-model",
        is_visible: true,
        supports_image_input: true,
      }),
    ];

    const merged = mergeFetchedModelConfigurations(fetched, existing);

    expect(merged).toEqual([
      expect.objectContaining({
        name: "vision-model",
        is_visible: true,
        supports_image_input: true,
        display_name: "Vision (refetched)",
      }),
      expect.objectContaining({
        name: "text-model",
        is_visible: false,
        supports_image_input: false,
      }),
    ]);
  });

  it("adds newly discovered models as unselected with fetched vision flag", () => {
    const existing = [
      makeModel({
        name: "known",
        is_visible: true,
        supports_image_input: true,
      }),
    ];
    const fetched = [
      makeModel({
        name: "known",
        is_visible: false,
        supports_image_input: false,
      }),
      makeModel({
        name: "new-vl",
        is_visible: true,
        supports_image_input: true,
      }),
    ];

    const merged = mergeFetchedModelConfigurations(fetched, existing);

    expect(merged).toEqual([
      expect.objectContaining({
        name: "known",
        is_visible: true,
        supports_image_input: true,
      }),
      expect.objectContaining({
        name: "new-vl",
        is_visible: false,
        supports_image_input: true,
      }),
    ]);
  });
});
